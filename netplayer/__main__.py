import sys
sys.path.append('~/netplayer')

from netplayer import *
import os


audioFiles = [
        os.path.join('E:/', 'Development', 'netplayer', 'audio_files', 'pinkNoise_01.wav'),
    ]
chunksize = 8192
port = 11125

def __serve():
    with NetPlayerServer(
            audioFiles, chunkSize=chunksize, port=port
        ) as server:
        server.run()
    print('Stopped server')


def __receive():
    with NetPlayerReceiver(
            bufferLength=352800, ringSize=8,
            chunkSize=chunksize, port=port
        ) as receiver:
        receiver.run()
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
 