#!/usr/bin/env python

import math
import collections
import threading
import statistics
import time
import struct
from multiprocessing import Lock
import pyaudio
import numpy as np


__all__ = [
        'RingBufferBase',
        'RingBuffer',
        'ThreadedRingBuffer',
        'FormatUnknown',
        'PlayableAudioDevice'
    ]

def visualize_line(state, lineLength=80):
    print(' '.join(
            (' ' * int((lineLength - 1) * float_to_scalar(state)),
            u'\u2058', str(state))
        ))

def visualize(samples, lineLength=80):
    for sample in samples:
        visualize_line(sample, lineLength)


def clip_value(value, minimum, maximum):
    if value < minimum:
        return minimum
    elif value > maximum:
        return maximum
    else:
        return value


def clip_float(value):
    if value > 1.0:
        return 1.0
    elif value < -1.0:
        return -1.0
    else:
        return value


def clip_int(value, bitDepth=8, signed=False):
    if signed:
        maximum = (2 ** (bitDepth - 1) - 1)
        minimum = -(maximum + 1)
    else:
        maximum = ((2 ** bitDepth) - 1)
        minimum = 0
    return clip_value(value, minimum, maximum)


def float_to_int(value, bitDepth=8, signed=False):
    value = clip_float(value)
    zero = (2 ** (bitDepth - 1))
    return round(value * (zero - 1 if value >= 0 else zero) + (0 if signed else zero))


def int_to_float(value, bitDepth=8, signed=False):
    pass


def linear_fade(state, target, length):
    step = -(state / length)
    for _ in range(length - 1):
        state += step
        yield state
        # Force the last value to mitigate floating point inaccuracies
    yield target


def _log_fade_scalar(state, target, length):
    if not 0 < (state - target) < 1:
        raise ValueError('Difference must be between 0 and 1')
    decay = math.exp(math.log(target / state) * (1.0 / length))
    for i in range(length):
        state *= decay
        yield state


def float_to_scalar(value):
    '''Conforms values ranging -1.0 to 1.0 to range 0.0 to 1.0'''
    return (value + 1) / 2


def scalar_to_float(value):
    '''Conforms values ranging 0.0 to 1.0 to range -1.0 to 1.0'''
    return (value * 2) - 1


def _log_fade(state, target, length):
    remainder = None
    conformed = (float_to_scalar(value) for value in (state, target))
    for i, multiplier in enumerate(_log_fade_scalar(*conformed, length - 1)):
        sample = scalar_to_float(state * multiplier)
        if sample < target:
            remainder = length - i
            break
        yield sample
    if remainder:
        for _ in range(remainder):
            yield target
    else:
        yield target


def route_mean_square(values):
    return math.sqrt(statistics.mean(value ** 2 for value in values))


def trim_left(iterable, lengthToDiscard):
    for i in range(lengthToDiscard, len(iterable)):
        yield iterable[i]

def sine(frequency, length, sampleRate, scale=1.0, radians=0):
    step = math.tau / (sampleRate / frequency)
    for _ in range(length):
        yield math.sin(radians) * scale
        radians += step


class FormatUnknown(Exception):
    pass


class RingBufferBase:
    def __init__(self, bufferLength=64, ringSize=8):
        self.ringSize = ringSize
        self.bufferLength = bufferLength

    def __str__(self):
        return '\n'.join((
                f'Buffer Length:\t{self.bufferLength}',
                f'Ring Size:\t\t{self.ringSize}'
            ))


class RingBuffer(RingBufferBase):
    def __init__(self, zero=0, **kwargs):
        super().__init__(**kwargs)
        self._zero = zero
        self._zeroes = collections.deque(
                self._zero for _ in range(self.bufferLength)
            )
        self.ring = collections.deque(maxlen=self.ringSize)
        for _ in range(self.ringSize):
            self.ring.append(collections.deque(maxlen=self.bufferLength))
        self.samplesWritten = 0
        self.samplesRemaining = self.bufferLength
        self.totalRingSampleLength = self.bufferLength * self.ringSize
        self._buffered = 0
        self._readIndex, self._writeIndex = 0, 1
        for buffer in self.ring:
            for _ in range(self.bufferLength):
                buffer.append(0)
        self.callback = None
    
    @property
    def _buffered(self):
        return self.__buffered
    
    @_buffered.setter
    def _buffered(self, val):
        self.__buffered = clip_value(
                val, 0, self.totalRingSampleLength
            )

    def buffered(self):
        return self._buffered

    def available(self):
        return self.totalRingSampleLength - self._buffered
    
    def _pad(self, filler=None, bufferIndex=None, sampleIndex=None):
        if filler is None:
            filler = self._zero
        if bufferIndex is None:
            bufferIndex = self._writeIndex
        if sampleIndex is None:
            sampleIndex = self.samplesWritten
        for i in range(sampleIndex, self.bufferLength):
            self.ring[bufferIndex][i] = filler
    
    def _fill_single(self, filler=None, bufferIndex=None):
        self._pad(filler, bufferIndex, 0)
    
    def fill(self, filler=None, force=False):
        if filler is None:
            filler = self._zero
        if force:
            for buffer in self.ring:
                for i in range(self.bufferLength):
                    buffer[i] = filler
        else:
            while self.writable():
                self._fill_single(filler)
                self.rotate_write_buffer()
    
    def __str__(self):
        return '\n'.join((
                super().__str__(),
                f'Read Index:\t\t{self._readIndex}',
                f'Write Index:\t\t{self._writeIndex}'
            ))
    
    def rotate_read_buffer(self):
        self._readIndex += 1
        print(f'Rotating read buffer to ring index {self._readIndex}')
        if self._readIndex >= self.ringSize:
            self._readIndex = 0
        self._buffered -= self.bufferLength
    
    def rotate_write_buffer(self):
        self._writeIndex += 1
        print(f'Rotating write buffer to ring index {self._writeIndex}')
        if self._writeIndex >= self.ringSize:
            self._writeIndex = 0
        self.samplesWritten = 0
        self.samplesRemaining = self.bufferLength
    
    def writable(self):
        return self._readIndex != self._writeIndex
    
    def _read(self):
        return self.ring[self._readIndex]
    
    def read(self):
        out = self._read()
        self.rotate_read_buffer()
        return out
    
    def _write(self, data):
        for i, sample in enumerate(data):
            self.ring[self._writeIndex][self.samplesWritten + i] = sample
        self.samplesWritten += len(data)
        self.samplesRemaining -= len(data)
        self._buffered += len(data)
        if not self.samplesRemaining:
            self.rotate_write_buffer()
        return len(data)
    
    def write_single(self, data, force=False):
        if not self.writable() and not force:
            return 0
        self.ring[self._writeIndex][self.samplesWritten] = data
        self.samplesWritten += 1
        self.samplesRemaining -= 1
        self._buffered += 1
        if not self.samplesRemaining:
            self.rotate_write_buffer()
        return 1

    def write(self, data, force=False):
        '''Writes around ring and returns number of samples written'''
        if len(data) <= self.samplesRemaining:
            return self._write(data)
        written = 0
        while data and (self.writable() or force):
            chunk = self._write(collections.deque(
                    data[i] for i in range(self.samplesRemaining)
                ) if len(data) > self.samplesRemaining else data)
            written += chunk
            data = collections.deque(trim_left(data, chunk))
        return written
    
    def write_direct(self, data, buffer):
        if len(data) > self.bufferLength:
            raise IndexError(f'Max length: {self.bufferLength}')
        for i, sample in enumerate(data):
            buffer[i] = sample
        return len(data)
    
    def write_pop(self, data):
        '''Writes to ring and returns any unwritten data'''
        written = self.write(data)
        if len(data) > written:
            if isinstance(data, collections.deque):
                return collections.deque(trim_left(data, written))
            else:
                return data[written:]


class ThreadedRingBuffer(RingBuffer):
    def __init__(self, sampleRate=44100, **kwargs):
        super().__init__(**kwargs)
        self.sampleRate = sampleRate
        self.__pauseLock = Lock()
        self.paused = None
        self.__threadLock = Lock()
        self.__threadRunning = False
        self.__terminationLock = Lock()
        self.__terminate = False
        self.bufferDuration = self.sampleRate / self.bufferLength
        self.rotater = None
        self.callback = kwargs.get('callback')

    @property
    def callback(self):
        return self.__callback

    @callback.setter
    def callback(self, func):
        self.__callback = func

    def _fade_out(self):
        return collections.deque(linear_fade(
                self.ring[self._readIndex][-1],
                self._zero,
                self.bufferLength
            ))

    def _read_timer(self):
        try:
            now = time.time_ns()
        except:
            now = time.time()
        last = now
        elapsed = 0
        faded = False
        while self.__threadRunning:
            try:
                now = time.time_ns()
            except:
                now = time.time()
            elapsed += now - last
            if elapsed >= self.bufferDuration:
                if self.__terminate:
                    self.callback(self._fade_out())
                    self.__threadRunning = False
                elif self.paused:
                    if not faded:
                        self.callback(self._fade_out())
                        faded = True
                    self.callback(self._zeroes)
                else:
                    self.callback(super().read())
                    if faded:
                        faded = False
                last = now

    def start_rotate_thread(self):
        self.rotater = threading.Thread(target=self._read_timer)
        self.__threadRunning = True
        self.__terminate = False
        self.paused = False
        self.rotater.start()

    def stop_rotate_thread(self):
        with self.__terminationLock:
            self.__terminate = True
        self.rotater.join()
    
    def pause(self):
        with self.__pauseLock:
            self.paused = True
    
    def resume(self):
        with self.__pauseLock:
            self.paused = False
    
    def running(self):
        return self.__threadRunning


class PlayableAudioDevice:
    _formatDefaultInferences = {
            8: pyaudio.paUInt8,
            16: pyaudio.paInt16,
            24: pyaudio.paInt24,
            32: pyaudio.paFloat32
        }
    _formatBitDepths = {
            pyaudio.paFloat32: 32,
            pyaudio.paInt32: 32,
            pyaudio.paInt24: 24,
            pyaudio.paInt16: 16,
            pyaudio.paInt8: 8,
            pyaudio.paUInt8: 8,
        }
    _formatZeroValues = {
            pyaudio.paFloat32: 0.0,
            pyaudio.paInt32: 0,
            pyaudio.paInt24: 0,
            pyaudio.paInt16: 0,
            pyaudio.paInt8: 0,
            pyaudio.paUInt8: 127,
            pyaudio.paCustomFormat: 0
        }
    
    def __init__(self, **kwargs):
        self._player = pyaudio.PyAudio()
        self.__streamOpen = False
        self.channels = None
        self.__sampWidth = None
        self.__frameRate = None
        self.bufferLength = None
        self.ringSize = None
        self.set_format()
        self.set_buffers(openStream=False)
        if any(kwargs):
            self.set_format(**kwargs)
            self.set_buffers(**kwargs)

    def __del__(self):
        self.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()

    @property
    def sampWidth(self):
        return self.__sampWidth
    
    @sampWidth.setter
    def sampWidth(self, val):
        self.__sampWidth = int(val)
        self.bitDepth = int(self.__sampWidth * 8)

    @property
    def bitDepth(self):
        return self.__bitDepth
    
    @bitDepth.setter
    def bitDepth(self, val):
        self.__bitDepth = val
        self.__format = self._formatDefaultInferences.get(val)
        self._zero = self._formatZeroValues[self.__format]

    @property
    def sampleRate(self):
        return self.frameRate

    @property
    def frameRate(self):
        return self.__frameRate
    
    @frameRate.setter
    def frameRate(self, val):
        self.__frameRate = int(val)
        self._frameDuration = 1 / self.frameRate

    @property
    def format(self):
        return self.__format

    @format.setter
    def format(self, audioFormat):
        if audioFormat not in self._formatZeroValues.keys():
            raise ValueError('Invalid format')
        else:
            self.__format = audioFormat
        if self.__format in self._formatBitDepths.keys():
            self.__bitDepth = self._formatBitDepths[self.__format]
        self._zero = self._formatZeroValues[self.__format]

    def set_format(
                self, channels=2, sampWidth=2,
                frameRate=44100, **kwargs
            ):
        print('Setting format')
        print(
                'Format args: ',
                channels, sampWidth,
                frameRate, kwargs
              )
        if (
                not kwargs and
                (channels, sampWidth, frameRate)
                != (self.channels, self.sampWidth, self.frameRate)
            ):
            self.channels = channels
            self.sampWidth = sampWidth
            self.frameRate = frameRate
        print('Format set')
    
    def set_buffers(
            self, bufferLength=4096, ringSize=8,
            openStream=True, **kwargs
        ):
        if (
                not kwargs
                and (bufferLength, ringSize)
                != (self.bufferLength, self.ringSize)
            ):
            print('Setting buffers')
            if bufferLength % self.channels:
                raise ValueError(
                    'Buffer length must be divisible by channels'
                )
            self.bufferLength = bufferLength
            self.ringSize = ringSize
            if self.__streamOpen:
                self.stream.stop_stream()
                self.stream.close()
            self.channelBuffers = collections.deque()
            for _ in range(self.channels):
                self.channelBuffers.append(RingBuffer(
                        bufferLength=int(self.bufferLength / self.channels),
                        ringSize=self.ringSize,
                        zero=self._zero,
                        **kwargs
                    ))
            self.interleaved = ThreadedRingBuffer(
                    sampleRate=self.frameRate,
                    bufferLength=self.bufferLength,
                    ringSize=self.ringSize,
                    zero=self._zero,
                    **kwargs
                )
            self.interleaved.callback = self._play
        if openStream:
            if self.__streamOpen:
                self.stream.stop_stream()
                self.stream.close()
            self.stream = self._player.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.frameRate,
                    output=True
                )
            self.__streamOpen = True
        else:
            self.stream = None
            self.__streamOpen = False
        self.__formatSet = True
        print('Buffers set')

    def start(self):
        self.interleaved.start_rotate_thread()

    def stop(self):
        print('Stopping audio')
        self.stream.stop_stream()
        self.stream.close()
        self.interleaved.stop_rotate_thread()
        print('Stopped audio')

    def running(self):
        return self.interleaved.running()
    
    def playing(self):
        return self.__streamOpen and not self.interleaved.paused

    def pause(self, seconds=None):
        self.interleaved.pause()
        if seconds is not None:
            time.sleep(seconds)
            self.interleaved.resume()

    def resume(self):
        self.interleaved.resume()

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        except:
            pass
        self._player.terminate()
        self.interleaved.stop_rotate_thread()

    def _play(self, data):

        # transformed = b''.join((np.float32(sample) for sample in data))
        transformed = b''.join((np.int16(sample) for sample in data))
        
        self.stream.write(transformed)

    def _interleave_channel_buffers(self):
        """
        if self.channels == 1:
            self.interleaved.write(self.channelBuffers[0].read())
        else:
            concatenated = (b.read() for b in self.channelBuffers)
            for i in range(self.bufferLength):
                for buffered in concatenated:
                    self.interleaved.write(buffered[i])
        """

    def buffered(self):
        return self.interleaved.buffered()
    
    def available(self):
        return self.interleaved.available()

    def wait(self, required=None):
        if not required:
            required = self.bufferLength
        while self.available() < required:
            time.sleep(self._frameDuration)

    def _write(self, data, **kwargs):
        self.interleaved.write(data, **kwargs)
        """
        frames = len(data) / self.sampWidth
        chunked = (
                [[data[index + i] for i in range(width)]
                for index in range(0, len(data), self.sampWidth)]
            )
        """
        """
        if self.channels == 1:
            self.channelBuffers[0].write(data)
        else:
            for chunk in data[0:-1:self.channels]:
                (channels,) = chunk
                for i, channelData in enumerate(channels):
                    self.channelBuffers[i].write_single(channelData)
        self._interleave_channel_buffers()
        """

    def write(self, data, **kwargs):
        """
        data = tuple(data)
        if len(data) % self.channels:
            raise IndexError('Invalid data length')
        """
        
        self._write(data, **kwargs)


if __name__ == '__main__':
    sampleRate = 44100
    bufferLength = 11025
    ringSize = 4

    with PlayableAudioDevice() as device:
        device.set_format(
                channels=1,
                format=pyaudio.paFloat32,
                frameRate=sampleRate
            )
        device.set_buffers(bufferLength=bufferLength, ringSize=ringSize)

        device.interleaved.write(tuple(
                sine(1000, sampleRate, sampleRate, .25)
            ), force=True)

        device.start()
        time.sleep(2)
