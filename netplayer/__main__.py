import sys
sys.path.append('~/netplayer')

from netplayer import *


async def print_chunk_len(chunk):
    print(f'Received chunk of len {len(chunk)}')
    await curio.sleep(0)


def __serve():
    with NetPlayerServer(['../audio_files/01_One More Time.wav'], chunkSize=8192) as server:
        server.run()
    print('Stopped server')


def __receive():
    with NetPlayerReceiver(bufferLength=8192, ringSize=16, chunkSize=8192) as device:
        device.run()
    print('Stopped receiver')


if __name__ == '__main__':
    try:
        command = sys.argv[1].lower()
    except IndexError:
        command = None
    if command in ['r', 'rec', 'recv', 'receive', 'receiver']:
        __receive()
    else:
        __serve()
