from pyringbuffer import *
import numpy as np
import collections
import time
import wave
import pyaudio

'''
TODO:
    join data instead of returning deque with read()
'''

with wave.open('../audio_files/01_One More Time.wav', 'rb') as wav:
    numFrames = wav.getnframes()
    framerate = wav.getframerate()
    data = wav.readframes(numFrames)
    samplewidth = wav.getsampwidth()
    channels = wav.getnchannels()
    filesize = numFrames * channels * samplewidth
    frameLength = channels * samplewidth
    frameDuration = (1 / framerate) / frameLength

print('Read')


chunksize = 128
chunkDuration = frameDuration * chunksize
ringSize = 12
ringIndex = 0
remaining = len(data)
i = 0
started = False

# chunked = collections.deque(
#         (collections.deque(data[index + i] for i in range(frameLength))
#          for index in range(0, len(data), frameLength))
#     )

# with PlayableAudioDevice(
#         zero=np.int16(0),
#         bufferLength=chunksize, ringSize=8,
#         channels=channels, sampWidth=samplewidth,
#         frameRate=framerate
#     ) as buff:
#     i = 0
#     remaining = len(data)
#     started = False

#     while remaining:
#         if remaining >= chunksize:
#             chunk = data[i:i + chunksize]
#         else:
#             chunk = data[i:]
#         i += len(chunk)
#         remaining -= len(chunk)
#         avail = buff.available()
#         if avail:
#             buff.write(chunk, force=False)
#         elif not started:
#             print('Starting')
#             buff.start()
#             started = True
#         del chunk


#     time.sleep(10)

#     buff.stop()

#     print('Done')

pa = pyaudio.PyAudio()
stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=framerate,
        output=True
    )

while remaining:
    if remaining >= chunksize:
        chunk = data[i:i + chunksize]
    else:
        chunk = data[i:]
    i += len(chunk)
    remaining -= len(chunk)
    if ringIndex < ringSize:
        stream.write(chunk)
    else:
        time.sleep(chunkDuration)
    del chunk
