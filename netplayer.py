#!/usr/bin/env python

import collections
import curio
from curio import socket
import itertools
import numpy as np
import os
import pyaudio
import sys
import threading
import wave
import queue


class AsyncNetBase:
    def __init__(self, host='', port=52345, udp=False, server=False, bufferSize=4096):
        self.alive = True
        self.bufferSize = bufferSize
        self.procs, self.tasks = set(), curio.TaskGroup(wait=any)
        self.lock = threading.Lock()
        self.socket = socket.socket(
                socket.AF_INET,
                (socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
            )
        self.port = port
        if server:
            self.socket.bind((host, self.port))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.alive = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, val):
        assert isinstance(val, int), 'Must be int'
        assert 0 < val <= 65535
        self._port = val

    @property
    def bufferSize(self):
        return self._bufferSize

    @bufferSize.setter
    def bufferSize(self, val):
        assert isinstance(val, int), 'Must be int'
        self._bufferSize = val

    async def add_tasks(self, *coroutines):
        for coroutine in coroutines:
            await self.tasks.spawn(curio.spawn(coroutine))

    async def join_tasks(self):
        async for task in self.tasks:
            await task.join()

    async def close(self):
        self.alive = False
        await self.tasks.cancel_remaining()
        await self.join_tasks()
        await self.socket.close()
        for proc in self.procs:
            try:
                proc.terminate()
            except AttributeError:
                pass
            proc.join()


class AsyncUdp(AsyncNetBase):
    def __init__(self, server=False, *args, **kwargs):
        super().__init__(udp=True, server=server, *args, **kwargs)
        self.sendBuffer = collections.deque()
        self.receiveBuffer = collections.deque()
        self.clients, self.active = {}, None
        self.buffered = False

    def process(self, client, data):
        if self.active != client:
            self.active = client
            if client not in self.clients.keys():
                print(f'Receiving from {client}')
                self.receiveBuffer = collections.deque()
                self.buffered = False
        self.receiveBuffer.append(data)
        if len(self.receiveBuffer) >= self.bufferSize:
            self.buffered = True

    async def receive(self):
        print('Starting receiver')
        while self.alive:
            try:
                data, client = await self.socket.recvfrom(self.bufferSize)
            except Exception as e:
                print(e)
            else:
                self.process(client[0], data)
            await curio.sleep(0)

    async def send(self, data, client, port=None):
        if port is None:
            port = self.port
        await self.socket.sendto(data, (client, port))

    async def send_from_buffer(self):
        data = client = None
        try:
            while self.alive:
                if self.sendBuffer:
                    try:
                        data, client = self.sendBuffer.popleft()
                    except Exception as e:
                        print(e)
                    else:
                        await self.send(data, client)
                await curio.sleep(0)
        except curio.CancelledError:
            return

    async def publish(self, data, port=None):
        async for client in self.clients.keys():
            await self.send(data, client, port)

    async def buffer(self, data, client):
        self.sendBuffer.append((data, client))


class NetPlayer(AsyncUdp):
    """Receive audio over a network"""

    def __init__(self, *args, **kwargs):
        super().__init__(server=True, *args, **kwargs)
        self.player = pyaudio.PyAudio()
        self.stream = self.player.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=48000,
                output=True
            )

    async def close(self):
        await super().close()
        self.stream.stop_stream()
        self.stream.close()
        self.player.terminate()

    async def play_buffer(self):
        while self.alive:
            await curio.sleep(0)
            if not self.buffered:
                continue
            if self.receiveBuffer:
                chunk = self.receiveBuffer.popleft()
                self.stream.write(chunk)

    async def listen(self):
        await curio.spawn(super().receive)
        await curio.spawn(self.play_buffer)
        while self.alive:
            await curio.sleep(0)
        await self.close()

    def start(self):
        curio.run(self.listen)

    def run(self):
        try:
            self.start()
        except KeyboardInterrupt:
            self.alive = False


class NetSender(AsyncUdp):
    """Send audio over a network"""

    def __init__(self, target='127.0.0.1', loop=False, *args, **kwargs):
        super().__init__(server=False, *args, **kwargs)
        self.target = target
        self.files = []
        self.loop = loop
        self.chunkSize = 1024

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, val):
        assert isinstance(val, str), 'Must be str'
        nums = val.split('.')
        assert (
                (len(nums) is 4)
                and (all(d.isdigit() for d in nums))
                or val is 'localhost'
            ), 'Invalid target address'
        self._target = val

    def add_files(self, *files):
        for filepath in files:
            assert isinstance(filepath, str), 'Must be str'
            assert os.path.exists(filepath), 'File not found'
            self.files.append(filepath)

    def scan(self, filepath, recursive=True):
        try:
            for f in os.scandir(filepath):
                if not f.is_dir():
                    self.add_files(f.path)
                elif recursive:
                    self.add_files(f.path)
        except NotADirectoryError:
            self.add_files(filepath)
        self.files.sort()

    def open(self, filename):
        print(f'Opening {filename}')
        with wave.open(filename, 'rb') as wav:
            chunk = wav.readframes(self.chunkSize)
            while chunk:
                # yield np.frombuffer(chunk, dtype=np.int16)
                yield chunk
                chunk = wav.readframes(self.chunkSize)
        print(f'{filename} buffered')

    async def buffer_file(self, filename):
        try:
            for chunk in self.open(filename):
                await (await curio.spawn(super().send(chunk, self.target))).join()
                # await super().buffer(chunk, self.target)
        except wave.Error:
            return

    async def run(self):
        # await super().add_tasks(super().send_from_buffer)
        for filename in (itertools.cycle(self.files) if self.loop else self.files):
            await self.buffer_file(filename)
        await super().close()

    def play_single(self, filename):
        self.add_files(filename)
        self.play()

    def play(self):
        curio.run(self.run)


if __name__ == '__main__':
    try:
        command = sys.argv[1]
    except IndexError:
        command = 'receive'
    if command.lower() in ['send', 'snd', 's']:
        with NetSender() as ns:
            ns.scan('./audio_files')
            ns.play()
    elif command.lower() in ['recv', 'rec', 'receive', 'r']:
        with NetPlayer() as server:
            server.run()
