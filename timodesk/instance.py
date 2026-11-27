"""Keep one owner of each Notes library and forward later launches to it."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time

from PyQt5.QtCore import QLockFile, QObject
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


class LibraryInstance(QObject):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        identity = os.path.normcase(str(root.resolve()))
        self.name = "HanLinNotes-" + hashlib.sha256(os.fsencode(identity)).hexdigest()
        self.lock = QLockFile(str(root.parent / ("." + self.name + ".lock")))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.handler = None
        self.pending = []
        self.primary = False

    def start(self, import_path):
        """Return 'primary', 'forwarded', or 'busy'."""
        deadline = time.monotonic() + 3
        packet = json.dumps(import_path).encode("ascii") + b"\n"
        while time.monotonic() < deadline:
            if self.lock.tryLock(0):
                QLocalServer.removeServer(self.name)
                if self.server.listen(self.name):
                    self.primary = True
                    return "primary"
                self.lock.unlock()
                return "busy"
            if self.lock.error() != QLockFile.LockFailedError:
                return "busy"
            socket = QLocalSocket(self)
            socket.connectToServer(self.name)
            if socket.waitForConnected(100):
                found, pid, _, _ = self.lock.getLockInfo()
                if sys.platform == "win32" and found and pid:
                    ctypes.windll.user32.AllowSetForegroundWindow(pid)
                socket.write(packet)
                socket.flush()
                sent = not socket.bytesToWrite() or socket.waitForBytesWritten(250)
                socket.disconnectFromServer()
                socket.deleteLater()
                return "forwarded" if sent else "busy"
            socket.abort()
            socket.deleteLater()
            time.sleep(0.05)
        return "busy"

    def set_handler(self, handler):
        self.handler = handler
        pending, self.pending = self.pending, []
        for path in pending:
            handler(path)

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(65537)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            self._read(socket)

    def _read(self, socket):
        if not socket.canReadLine():
            if socket.bytesAvailable() > 65536:
                socket.abort()
            return
        try:
            path = json.loads(bytes(socket.readLine(65536)).decode("ascii"))
        except (ValueError, UnicodeError):
            socket.abort()
            return
        socket.disconnectFromServer()
        if not isinstance(path, str):
            return
        if self.handler is None:
            self.pending.append(path)
        else:
            self.handler(path)

    def close(self):
        if self.primary:
            self.server.close()
            self.lock.unlock()
            self.primary = False
