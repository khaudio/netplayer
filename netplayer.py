#!/usr/bin/env python

# TODO:
#     client dataclass with meta
#     sender subclass + playing

import collections
import curio
from curio import socket
from dataclasses import dataclass
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
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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
            await self.tasks.spawn(coroutine)

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


class DefaultClient(collections.defaultdict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update({'time': None, 'latency': 0})



class AsyncUdp(AsyncNetBase):
    def __init__(self, server=False, monitor=True, monitorPort='', *args, **kwargs):
        super().__init__(udp=True, server=server, *args, **kwargs)
        self.sendBuffer = collections.deque()
        self.receiveBuffer = collections.deque()
        self.clients = collections.defaultdict(DefaultClient)
        self.active = None
        self.latency = None
        self.buffered = False
        self.monitorPort = (self.port + 1) if not monitorPort else monitorPort
        if monitor:
            self._udp.bind(('', self.monitorPort))

    @property
    def latency(self):
        return self._latencyOverride if self._latencyOverride is not None else 0
    
    @latency.setter
    def latency(self, val):
        if val is not None:
            assert isinstance(val, (int, float)), 'Must be int or float'
        self._latencyOverride = val

    def update_client(self, client, meta=None):
        if client not in self.clients.keys():
            print(f'Found {client}')
            self.clients[client]
        if meta:
            self.clients[client].update(meta)
        self.latency = max(val['latency'] for val in self.clients.values())

    def process(self, client, data):
        self.update_client(client)
        if self.active != client:
            print(f'Receiving from {client}')
            self.active = client
            self.receiveBuffer, self.buffered = collections.deque(), False
        self.receiveBuffer.append(data)
        if len(self.receiveBuffer) >= self.bufferSize:
            self.buffered = True

    async def receive(self, buffer=True, sock=None):
        if not sock:
            sock = self.socket
        while self.alive:
            try:
                data, client = await sock.recvfrom(self.bufferSize)
            except Exception as e:
                print(e)
            else:
                if buffer:
                    self.process(client[0], data)
                else:
                    return client, data
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
        for client in self.clients.keys():
            await self.send(data, client, port)

    async def broadcast(self, data, client='255.255.255.255', port=None):
        port = self.monitorPort if not port else port
        await self.send(data, client, port)
    
    async def monitor(self):
        while self.alive:
            client, data = await self.receive(buffer=False, sock=self._udp)
            try:
                meta = json.loads(data)
                meta['latency'] = time.time() - meta['time']
            except Exception as e:
                print(e)
            else:
                print('Found client {client}')
                self.update_client(client, meta)
            curio.sleep(0)

    async def buffer(self, data, client):
        self.sendBuffer.append((data, client))


class NetPlayer(AsyncUdp):
    """Receive audio over a network"""

    def __init__(self, *args, **kwargs):
        super().__init__(server=True, monitor=True, *args, **kwargs)
        self.player = pyaudio.PyAudio()
        self.stream = self.player.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=44100,
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
        print('Starting receiver')
        await super().add_tasks(super().receive, super().monitor, self.play_buffer)
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
        monitor = (target not in ('127.0.0.1', '127.0.1.1', 'localhost'))
        super().__init__(server=False, monitor=monitor, *args, **kwargs)
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
                await (await self.tasks.spawn(super().send(chunk, self.target))).join()
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


@dataclass
class Client:
    latency: int = 0


def main():
    try:
        command = sys.argv[1]
    except IndexError:
        command = 'receive'
    try:
        assets = sys.argv[3]
    except IndexError:
        assets = './audio_files'
    if command.lower() in ['send', 'snd', 's']:
        with NetSender() as ns:
            ns.scan(assets)
            ns.play()
    elif command.lower() in ['recv', 'rec', 'receive', 'r']:
        with NetPlayer() as server:
            server.run()

if __name__ == '__main__':
    main()