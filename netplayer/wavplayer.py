from pyringbuffer import *
import numpy as np
import collections
import time
import wave

with wave.open('../audio_files/01_One More Time.wav', 'rb') as wav:
    numFrames = wav.getnframes()
    framerate = wav.getframerate()
    data = wav.readframes(framerate * 12)
    samplewidth = wav.getsampwidth()
    channels = wav.getnchannels()
    filesize = numFrames * channels * samplewidth

print('Read')

frameLength = channels * samplewidth
chunked = collections.deque(
        (collections.deque(data[index + i] for i in range(frameLength))
         for index in range(0, len(data), frameLength))
    )

print('Chunked')

print(chunked[0])

buff = PlayableAudioDevice(zero=np.int16(0))
buff.set_format(channels, samplewidth, framerate)
buff.set_buffers(bufferLength=22050, ringSize=8)

for frame in chunked:
    if buff.available():
        buff.write(frame, force=True)

buff.start()

time.sleep(4)

buff.stop()

print('Buffer started')


print('Done')
