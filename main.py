import datetime
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import typing
from urllib.parse import urlparse, parse_qs

import yaml

git_repository = "git@gitlab.com:git-docker/example.git"
git_branch = "main"
home_folder = "../../tmp"
env_folder = "../../envs"
http_enabled = True
http_port = 8000
http_token = "example"


def parse_filepath(path):
    return path.split('/', maxsplit=2)


def parse_yaml(file):
    with open(file, "r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
            exit(3)


def add_service_action(service, action):
    if not hasattr(services, service):
        services[service] = list()
    last_action = services[service].pop() if len(services[service]) > 0 else ''
    if last_action == action:
        services[service].append(action)
    else:
        if action == 'up':
            services[service].append('up')
        if action == 'down':
            services[service].clear()
            services[service].append('down')
        if action == 'restart' and last_action == '':
            services[service].append('restart')


def update_env(service):
    if os.path.exists(env_folder + "/" + service + ".env"):
        shutil.copyfile(env_folder + "/" + service + ".env", git_directory + "/" + service + "/.env")
    else:
        if os.path.exists(git_directory + "/" + service + "/.env"):
            os.remove(git_directory + "/" + service + "/.env")


def compose_restart(service):
    folder = git_directory + "/" + service
    if os.path.exists(folder + "/docker-compose.yml"):
        update_env(service)
        print("Restart " + service)
        subprocess.run(["docker-compose", "restart"], cwd=folder)


def compose_up(service):
    folder = git_directory + "/" + service
    if os.path.exists(folder + "/docker-compose.yml"):
        update_env(service)
        print("Up " + service)
        subprocess.run(["docker-compose", "up", "-d", "--quiet-pull", "--remove-orphans"], cwd=folder)


def compose_down(service):
    folder = git_directory + "/" + service
    if os.path.exists(folder + "/docker-compose.yml"):
        print("Down " + service)
        subprocess.run(["docker-compose", "down", "--remove-orphans"], cwd=folder)


def git_exists():
    if os.path.exists(git_directory):
        status = subprocess.run(["git", "status"],
                                capture_output=True, cwd=git_directory)
        if status.returncode == 0:
            return True

        os.rmdir(git_directory)

    return False


def git_init():
    print("Clone git repository")
    subprocess.run(["git", "clone", git_repository],
                   capture_output=True, cwd=home_folder)

    # noinspection PyUnresolvedReferences
    for s in [f.path for f in os.scandir(git_directory) if f.is_dir() and f.name != ".git"]:
        add_service_action(s.split('/')[-1:][0], 'up')


def git_updates():
    print("Check updates")
    subprocess.run(["git", "fetch", "origin"],
                   capture_output=True, cwd=git_directory)
    result = subprocess.run(
        ["git", "log", "--reverse", "--name-status", "--oneline", git_branch + "..origin/" + git_branch],
        capture_output=True, cwd=git_directory)

    for change in [s.split(b'\t') for s in result.stdout.splitlines()]:
        if len(change) == 1 or '/' not in change[1].decode():
            continue

        s, file = parse_filepath(change[1].decode())
        if file == 'docker-compose.yml':
            if change[0] == b'A' or change[0] == b'M':
                add_service_action(s, 'up')
            elif change[0] == b'D':
                add_service_action(s, 'down')
            elif change[0][:1] == b'R':
                s2, file2 = parse_filepath(change[2].decode())
                if s != s2:
                    add_service_action(s, 'down')
                    if file2 == 'docker-compose.yml':
                        add_service_action(s2, 'up')
                    else:
                        add_service_action(s2, 'restart')
            else:
                print("Unknown operation '" + change[0].decode() + "'")
                exit(2)
        else:
            add_service_action(s, 'restart')
            if len(change) > 2:
                s2, file2 = parse_filepath(change[2].decode())
                if file2 == 'docker-compose.yml':
                    add_service_action(s2, 'up')
                else:
                    add_service_action(s2, 'restart')


def git_pull():
    print("Pull updates")
    subprocess.run(["git", "pull", "origin"],
                   capture_output=True, cwd=git_directory)


def get_settings():
    if os.path.exists(git_directory + "/settings.yml"):
        return dict(parse_yaml(git_directory + "/settings.yml"))
    else:
        return dict()


def sort_services(settings):
    s_services = dict()
    if 'start' in settings:
        for s in settings['start']:
            if s in services:
                s_services[s] = services.pop(s)
    for s in sorted(services):
        s_services[s] = services.pop(s)

    return s_services


def process():
    services.clear()
    if not git_exists():
        git_init()

        sorted_services = sort_services(get_settings())
    else:
        git_updates()

        sorted_services = sort_services(get_settings())
        print("Removing containers...")
        for service in sorted_services:
            for action in sorted_services[service]:
                if action == 'down':
                    compose_down(service)

        git_pull()

    print("Updating containers...")
    for service in sorted_services:
        for action in sorted_services[service]:
            if action == 'up':
                compose_up(service)
            if action == 'restart':
                compose_restart(service)


class GetHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.do_request()

    def do_POST(self):
        self.do_request()

    def do_request(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.finish()
        self.connection.close()
        self.wfile = typing.BinaryIO()
        parsed_url = urlparse(self.path)
        qs = parse_qs(parsed_url.query)
        token = qs.get('token')
        service = qs.get('service')

        if token is not None and token[0] == http_token:
            if parsed_url.path == '/update':
                ip = self.headers.get('X-Real-IP')
                if ip is None:
                    ip = self.headers.get('X-Forwarded-For')
                if ip is None:
                    ip = self.client_address[0]
                print("--------------------------------------------------")
                print(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]"), "Request update from", ip)
                process()
                print(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]"), "That's all")
            if service is not None:
                if parsed_url.path == '/start':
                    compose_up(service[0])
                if parsed_url.path == '/stop':
                    compose_down(service[0])
                if parsed_url.path == '/restart':
                    compose_restart(service[0])

    def log_message(self, nformat, *args):
        return


if __name__ == "__main__":
    matches = re.match(".+/(.+)\\.git", git_repository)
    if matches is None:
        print("Please check GIT_REPOSITORY")
        exit(1)

    git_folder = matches.groups()[0]
    git_directory = home_folder + "/" + git_folder
    services = dict()

    if http_enabled:
        Handler = GetHandler

        with socketserver.TCPServer(("", http_port), Handler) as httpd:
            try:
                print("Serving at port", http_port)
                httpd.serve_forever()
            except KeyboardInterrupt:
                print('^C received, shutting down server')
                httpd.socket.close()
    else:
        process()
