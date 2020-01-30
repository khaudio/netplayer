import wave
import curio
import json
import collections
import sys
import dataclasses
import time
import inspect
from pyringbuffer import *


class InvalidCallback(Exception):
    pass


@dataclasses.dataclass
class Client:
    def __init__(self, client):
        self.client = client
        self.index = 0
        self.ready = False
        self.acknowledged = False
        self.latencyNs = 0
    
    def __str__(self):
        return self.client.__str__()


class NetplayerBase:
    def __init__(self, port=25000, chunkSize=4096):
        self.alive = None
        self.chunkSize = chunkSize
        self.port = port
        self.__filename = None
        self.__filesize = None
        self.__framecount = 0
        self.index = 0
        self.paused = False
        self._ready_signal = json.dumps({
                'ready': True
            }).encode('utf-8')
        self.eof = json.dumps({'end': True}).encode('utf-8')
        self.__pause_signal = (
                json.dumps({'paused': True}).encode('utf-8')
            )
        self.__resume_signal = (
                json.dumps({'paused': False}).encode('utf-8')
            )
    
    def __del__(self):
        self.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()
        
    def close(self):
        self.alive = False

    @property
    def chunkSize(self):
        return self.__chunkSize
    
    @chunkSize.setter
    def chunkSize(self, value):
        if not isinstance(value, int):
            try:
                value = int(value)
            except:
                raise TypeError('Must be int')
        self.__chunkSize = value

    @property
    def filename(self):
        return self.__filename

    @filename.setter
    def filename(self, value):
        if not isinstance(value, str):
            raise TypeError('Must be str')
        self.__filename = value

    @property
    def filesize(self):
        return self.__filesize

    @filesize.setter
    def filesize(self, value):
        if not isinstance(value, int):
            try:
                value = int(value)
            except:
                raise TypeError('Must be int')
        self.__filesize = value

    @property
    def framecount(self):
        return self.__framecount

    @framecount.setter
    def framecount(self, value):
        if not isinstance(value, int):
            try:
                value = int(value)
            except:
                raise TypeError('Must be int')
        self.__framecount = value

    @property
    def index(self):
        return self.__index

    @index.setter
    def index(self, value):
        if not isinstance(value, int):
            try:
                value = int(value)
            except:
                raise TypeErroFormatUnknownr('Must be int')
        self.__index = value


class AudioServer(NetplayerBase):
    def __init__(self, filenames, chunkSize=4096, **kwargs):
        print('Starting audio server')
        super().__init__(chunkSize=chunkSize, **kwargs)
        self.parameters = None
        self.clients = collections.deque()
        self.files = collections.deque(filenames)
        self.filename = self.files[0]
        self.load_asset(self.filename)
        self.alive = True

    def load_asset(self, filename=None):
        if filename:
            if filename not in self.files:
                self.files.append(filename)
            self.filename = filename
        elif not self.filename:
            self.filename = self.files[0]
        self.index = 0
        with wave.open(self.filename, 'rb') as wav:
            self.framecount = wav.getnframes()
            self.asset = wav.readframes(self.framecount)
            self.filesize = len(self.asset)
            self.parameters = wav.getparams()[0:3]
        for client in self.clients:
            client.index = 0
    
    def _get_clients(self, socketclient):
        if not isinstance(socketclient, curio.io.Socket):
            raise TypeError('Must be socket')
        for client in self.clients:
            if client.client == socketclient:
                yield client

    def _get_socket_clients(self, socketclient):
        if not isinstance(socketclient, curio.io.Socket):
            raise TypeError('Must be socket')
        for client in self.clients:
            if client.client == socketclient:
                yield client.client

    async def _pause_client(self, client):
        await client.sendall(self.__pause_signal)

    async def _resume_client(self, client):
        await client.sendall(self.__resume_signal)

    async def pause(self):
        for client in self.clients:
            await self._pause_client(client)

    async def resume(self):
        for client in self.clients:
            await self._resume_client(client)

    def _encode_header(self):
        return json.dumps({
                'filename': self.filename,
                'framecount': self.framecount,
                'filesize': self.filesize,
                'format': self.parameters
            }).encode('utf-8')

    def _decode_request(self, data):
        request = json.loads(data)
        for attribute in ('name', 'index', 'chunksize'):
            if not attribute in request.keys():
                return
        return request

    async def _send_end_of_asset(self, client):
        print('Sending end of asset')
        await client.client.sendall(self.eof)
        client.ready = False
        client.acknowledged = False
        self.index = 0
        print(f'Sent end of asset')

    def _calculate_client_latency(self, client):
        pass

    async def send_asset(self, client):
        print(f'Sending asset')
        while self.alive:
            data  = await client.client.recv(1000)
            if data:
                try:
                    print(f'Waiting for request')
                    request = self._decode_request(data.decode())
                    name = request['name']
                    client.index = int(request['index'])
                    chunkSize = int(request['chunksize'])
                    print(
                            f'Received request with info:',
                            f'{name}, {client.index}, {chunkSize}'
                        )
                except:
                    print(f'Request not interpreted: {data}')
                    break
                del data
                try:
                    print(f'Server index: {self.index}')
                    chunk = bytearray(self.asset[
                            self.index:self.index + self.chunkSize
                        ])
                except IndexError:
                    print('Sending last chunk')
                    chunk = bytearray(self.asset[self.index:])
                if chunk:
                    print(
                            f'Sending chunk of size {len(chunk)}',
                            f'at index {self.index} to',
                            f'{self.index + self.chunkSize}'
                        )
                    self.index += len(chunk)
                    if self.index > self.filesize:
                        print(
                            f'Index {self.index}'
                            + f' exceeded filesize {self.filesize}'
                        )
                        break
                    await client.client.sendall(chunk)
                else:
                    print('Breaking')
                    break
            await curio.sleep(0)
        await self._send_end_of_asset(client)
        print('Sent asset')

    async def send_header(self, client, addr):
        print(f'Sending header to {addr}')
        await client.sendall(self._encode_header())
        print(f'Waiting for ready signal from {addr}')
        data = await client.recv(1000)
        decoded = None
        if data:
            print(f'Data: {data}')
            decoded = json.loads(data)
        if decoded:
            ready = decoded.get('ready')
            if ready:
                print(f'Got ready signal from {addr}')
                return True

    async def serve_asset(self, client, addr):
        client.ready = False
        client.acknowledged = False
        decoded = None
        while self.alive:
            if not client.ready:
                client.ready = await self.send_header(
                        client.client, addr
                    )
            elif client.ready and not client.acknowledged:
                await client.client.sendall(self._ready_signal)
                client.acknowledged = True
            elif all(c.acknowledged for c in self.clients):
                print('All clients ready and acknowledged')
                await self.send_asset(client)
            else:
                for client in self.clients:
                    if not client.acknowledged:
                        print(f'Client not ackd: {client}')
                print(f'Clients:\n\t{tuple(c for c in self.clients)}')
        await curio.sleep(0)

    async def serve_all_assets(self, client, addr):
        try:
            while self.alive:
                if client not in tuple(self._get_socket_clients(client)):
                    newClient = Client(client)
                    self.clients.append(newClient)
                    print(f'Adding client {addr} to clients')
                    print('\n\t'.join((c.__str__() for c in self.clients)))
                else:
                    [newClient] = tuple(self._get_clients(client))
                await self.serve_asset(newClient, addr)
                if all(
                        c.index >= self.filesize
                        for c in self.clients
                    ):
                    print('Advancing to next file')
                    self.files.rotate()
                    self.filename = self.files[0]
                    self.load_asset(self.filename)
                await curio.sleep(0)
        except BrokenPipeError or ConnectionResetError:
            print(f'{addr} disconnected')
        finally:
            if newClient in self.clients:
                self.clients.remove(newClient)
            print(f'Disconnecting {addr}')
            await client.close()


class AudioReceiver(NetplayerBase):
    def __init__(self, host='127.0.0.1', **kwargs):
        print('Starting audio receiver')
        super().__init__(**kwargs)
        self.host = host
        self.ready = False
        self.receiving = False
        # self.queue = collections.deque()
        self._receivedLastChunk = False
        self._sent_ready = False
        self._requested = False
        self._receivedCommand = False
        self.alive = True

    def _encode_request(self):
        return json.dumps({
            'name': self.filename,
            'index': self.index,
            'chunksize': self.chunkSize
        }).encode('utf-8')

    # def request(filename, immediate=False):
    #     if immediate:
    #         self.queue = collections.deque((filename,))
    #     else:
    #         self.queue.append(filename)

    async def send_ready(self, sock):
        print('Sending ready signal')
        self.index = 0
        self._receivedLastChunk = True
        if not self._sent_ready:
            await sock.sendall(self._ready_signal)
            self._sent_ready = True
        ack = await sock.recv(1000)
        if ack == self._ready_signal:
            self.ready = True

    async def request_chunk(self, sock):
        print('Requesting chunk')
        self._receivedLastChunk = False
        if not self._requested:
            await sock.sendall(self._encode_request())
            self._requested = True
    
    def _get_header(self, data):
        try:
            header = json.loads(data)
            self.filename = header['filename']
            self.framecount = int(header['framecount'])
            self.filesize = int(header['filesize'])
            self.remaining = self.filesize
            self.__parameters = header['format']
            print(f'Audio format received in header: {self.__parameters}')
        except:
            return
        else:
            print(f'Got header: {header}')
            return True

    async def _wait_for_ready(self, sock):
        print('Waiting for data')
        data = await sock.recv(1000)
        if data:
            if self._get_header(data):
                await self.send_ready(sock)
                return True

    def _decode_commands(self, encoded):
        try:
            decoded = json.loads(encoded)
        except:
            return
        else:
            print(f'Decoded: {decoded}')
            return decoded

    async def _receive_chunks(self, sock):
        if self._receivedLastChunk:
            await self.request_chunk(sock)
        chunk = await sock.recv(self.chunkSize)
        if chunk:
            decoded = self._decode_commands(chunk)
            if not decoded:
                print(
                    f'Got chunk of size: {len(chunk)},',
                    f'Index: {self.index}'
                )
                self.index += len(chunk)
                self.remaining -= len(chunk)
                self._receivedLastChunk = True
                yield chunk
            else:
                if decoded.get('end'):
                    print(f'Got EOF')
                    self._unready()
                if decoded.get('pause'):
                    print(f'Pausing playback')
                    self.paused = paused
                if self._get_header(chunk):
                    self._unready()
            self._requested = False
        else:
            return

    def _unready(self):
        self.ready = False
        self._sent_ready = False
        self.receiving = False

    async def retrieve(self, sock):
        while self.alive:
            if not self.ready:
                if await self._wait_for_ready(sock):
                    self.receiving = True
            elif self.receiving:
                async with (
                    curio.meta.finalize(self._receive_chunks(sock))
                ) as receiver:
                    async for chunk in receiver:
                        yield chunk
            await curio.sleep(0)

    async def run(self, callback=None):
        try:
            callbackIsCoroutine = inspect.iscoroutinefunction(callback)
        except:
            pass
        if callback:
            if not callable(callback):
                raise InvalidCallback('Callback must be callable')
            elif not inspect.signature(callback).parameters:
                raise InvalidCallback(
                        'Callback must accept at least one argument'
                    )
        while self.alive:
            try:
                sock = await curio.open_connection(self.host, self.port)
                async with sock:
                    async with (
                        curio.meta.finalize(self.retrieve(sock))
                    ) as retriever:
                        async for chunk in retriever:
                            if callbackIsCoroutine:
                                await callback(chunk)
                            elif callback:
                                callback(chunk)
                            await curio.sleep(0)
            except ConnectionResetError or BrokenPipeError:
                print('Disconnected; reconnecting...')
            except:
                print('Stopping run')
                raise
            else:
                print('Exited successfully')
            finally:
                print('Closing socket')
                await sock.shutdown(curio.socket.SHUT_RDWR)
                await sock.close()


class NetPlayerReceiver(PlayableAudioDevice, AudioReceiver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        AudioReceiver.__init__(self, *args, **kwargs)
        print('NetPlayerReciever initialized')
    
    def _get_header(self, *args, **kwargs):
        super()._get_header(*args, **kwargs)
        print('Setting NetPlayerReceiver format')
        super().set_format(*self.__parameters)
        print('Setting NetPlayerReceiver buffers')
        super().set_buffers(**kwargs)
        print('Starting output buffer')
        super().start()

    def run(self):
        print('Starting')
        curio.run(super().run, self.write)
        print('Done with async run')

    def write(self, data):
        if data is None:
            return
        super().write(data)
    
        print(data)
        time.sleep(1)


class NetPlayerServer(AudioServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def run(self):
        curio.run(
                curio.tcp_server, '',
                self.port, super().serve_all_assets
            )


async def print_chunk_len(chunk):
    print(f'Received chunk of len {len(chunk)}')
    await curio.sleep(0)


def serve():
    with NetPlayerServer(['../audio_files/pinkNoise_01.wav']) as server:
        server.run()
    print('Stopped server')


def receive():
    with NetPlayerReceiver() as device:
        device.run()
    print('Stopped receiver')


if __name__ == '__main__':
    try:
        command = sys.argv[1].lower()
    except IndexError:
        command = None
    if command in ['r', 'rec', 'recv', 'receive', 'receiver']:
        receive()
    else:
        serve()

