"""Запускает локальные анализаторы с запретом сетевых соединений в Python-процессе."""

import runpy
import socket
import sys


def deny_network(*args, **kwargs):
    """Останавливает сетевой вызов; stdio и локальные Unix-сокеты доступны."""
    raise OSError("Code Atlas: сетевые обращения отключены в офлайн-режиме")


class OfflineSocket(socket.socket):
    """Сохраняет локальный IPC, запрещая IP-соединения и отправку датаграмм."""

    def connect(self, address):
        """Разрешает подключение только через Unix-сокет."""
        if self.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return super().connect(address)

    def connect_ex(self, address):
        """Применяет ту же проверку к неблокирующему подключению."""
        if self.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return super().connect_ex(address)

    def sendto(self, *args, **kwargs):
        """Запрещает обход ограничения через UDP."""
        if self.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return super().sendto(*args, **kwargs)

    def sendmsg(self, *args, **kwargs):
        """Запрещает отправку IP-пакетов через sendmsg."""
        if self.family in (socket.AF_INET, socket.AF_INET6):
            deny_network()
        return super().sendmsg(*args, **kwargs)


def main():
    """
    Выполняет только согласованные модули анализа без доступа к сети.
    """
    allowed = {"graphify", "graphify.serve", "atlas.graphistry_worker", "graphify-chunks"}
    if len(sys.argv) < 2 or sys.argv[1] not in allowed:
        raise SystemExit("Ожидается модуль graphify, graphify.serve или atlas.graphistry_worker")
    module = sys.argv[1]
    sys.argv = sys.argv[1:]
    socket.socket = OfflineSocket
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    socket.gethostbyname = deny_network
    socket.gethostbyname_ex = deny_network
    if module == 'graphify-chunks':
        from .runtime_paths import resource_root
        script = resource_root() / 'resources' / 'integrations' / 'graphify' / 'chunk_worker.py'
        runpy.run_path(str(script), run_name='__main__')
    else:
        runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
