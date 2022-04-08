import cv2
import time
import numpy as np
from typing import Union, Callable
from queue import Queue
from threading import Thread, Event, Lock
from .sevent import Emitter
from .stream import MJPEGEncoder


class Camera(Emitter):
    """Camera device object, based on threading.

    Args:
        src: device source
        name: device name
        reconnectDelay: wait time for try reopen camera device
        fps: frames per second
        verbose: display info messages?
        size: tuple or list with a dimension of the image
        emitterIsEnabled: disable on/emit events (callbacks execution)
        enableBackground: if some error is produced with the camera it will display
                                a black frame with a message.
        queueModeEnabled: enable queue mode?
        queueMaxSize: queue maxsize

    Example usage:
        camera = Camera(src=0)
        camera.on('frame-ready', lambda frame: print('frame is ready:', type(frame)))
    """

    def __init__(
        self,
        src: Union[int, str] = 0,
        name: str = "default",
        reconnectDelay: Union[int, float] = 5,
        fps: Union[int, None] = 10,
        verbose: bool = False,
        size: Union[list, tuple, None] = None,
        flipX: bool = False,
        flipY: bool = False,
        emitterIsEnabled: bool = False,
        backgroundIsEnabled: bool = False,
        queueModeEnabled: bool = False,
        queueMaxSize: int = 96,
        text: str = "Device not available",
        font: None = cv2.FONT_HERSHEY_SIMPLEX,
        fontScale: Union[int, float] = 0.5,
        fontColor: Union[tuple, list] = [255, 255, 255],
        thickness: int = 1,
        processing: Callable = None,
        processingParams: dict = {},
        encoderIsEnabled: bool = True,
        encoderParams: dict = {},
        *args,
        **kwargs
    ):
        super(Camera, self).__init__(emitterIsEnabled=emitterIsEnabled, *args, **kwargs)
        self.src = src
        self.name = name
        self.frame = None
        self.fps = fps
        self.verbose = verbose
        self.size = size
        self.flipX = flipX
        self.flipY = flipY
        self.backgroundIsEnabled = backgroundIsEnabled
        self.background = None
        self.processing = processing
        self.processingParams = processingParams
        self.queueModeEnabled = queueModeEnabled
        self.queue = Queue(maxsize=queueMaxSize)

        self.frame64 = None
        self.encoderIsEnabled = encoderIsEnabled
        self.encoder = MJPEGEncoder(**encoderParams)

        if self.fps is not None:
            self.delay = 1 / self.fps
        else:
            self.delay = 0.1

        self.defaultDelay = self.delay
        self.reconnectDelay = reconnectDelay
        self.defaultSize = [720, 1280, 3]

        self.emitterIsEnabled = emitterIsEnabled
        self.device = None
        self.thread = Thread(target=self.run, name="camera-thread", daemon=True)

        self.running = Event()
        self.pauseEvent = Event()
        self.readEvent = Event()
        self.readLock = Lock()

        self.resume()

        if self.backgroundIsEnabled:
            self.enableBackground(
                size=self.size,
                text=text,
                font=font,
                fontColor=fontColor,
                fontScale=fontScale,
                thickness=thickness,
            )

    def __del__(self):
        self.stop()

    def createBackground(
        self,
        text: str = "Device not available",
        font: None = cv2.FONT_HERSHEY_SIMPLEX,
        fontScale: Union[int, float] = 0.5,
        fontColor: Union[tuple, list] = [255, 255, 255],
        thickness: int = 1,
        size: Union[tuple, list, None] = None,
    ):
        """It creates a custom background as numpy array (image) with a text message.

        Args:
            text: message to be displayed on the center of the background.
            font: cv2 font family
            fontScale: scale of the font
            fontColor: color of the font
            thickness: thickness of the font
            size: tuple, list with the size of the background.
                        If it's None, size will be set automatically.
        """
        if size is None:
            size = self.defaultSize

        if len(size) == 2:
            size = size[::-1]
            size.append(3)

        background = np.zeros(size, dtype=np.uint8)
        textsize = cv2.getTextSize(text, font, fontScale, thickness)[0]
        h, w, _ = size
        tw, th = textsize
        origin = (int((w - tw) / 2), int((h - th) / 2))
        background = cv2.putText(
            background, text, origin, font, fontScale, fontColor, thickness
        )
        return background

    def enableBackground(
        self,
        text: str = "Device not available.",
        font: None = cv2.FONT_HERSHEY_COMPLEX,
        fontScale: Union[int, float] = 2,
        fontColor: Union[tuple, list] = [255, 255, 255],
        thickness: int = 3,
        size: Union[tuple, list, None] = None,
    ):
        """It enables background as a frame.

        Args:
            text: message to be displayed on the center of the background.
            font: cv2 font family
            fontScale: scale of the font
            fontColor: color of the font
            thickness: thickness of the font
            size: tuple, list with the size of the background. If it's None,
                        size will be set automatically.
        """
        self.backgroundIsEnabled = True
        self.background = self.createBackground(
            text=text,
            font=font,
            fontScale=fontScale,
            fontColor=fontColor,
            thickness=thickness,
            size=size,
        )

    def disableBackground(self):
        """It disables the background."""
        self.backgroundIsEnabled = False
        self.background = None

    def start(self):
        """It starts the read loop."""
        self.thread.start()
        return self

    def getProcessingParams(self):
        """It returns processings params (kwargs)."""
        if self.processingParams is None:
            self.processingParams = {}
        return self.processingParams

    def hasProcessing(self):
        """It checks if a processing function is available."""
        return self.processing is not None

    def isConnected(self) -> bool:
        """It checks if a camera device is available."""
        if self.device is not None:
            return self.device.isOpened()
        return False

    def loadDevice(self):
        """It loads a camera device."""
        try:
            self.device = cv2.VideoCapture(self.src)
        except cv2.error as e:
            print("Exception: ", e)

    def reconnect(self):
        """It tries to reconnect with the camera device."""
        self.readEvent.clear()
        self.loadDevice()
        time.sleep(self.reconnectDelay)
        self.readEvent.set()

    def resume(self):
        """It resumes the read loop."""
        self.pauseEvent.set()

    def pause(self):
        """It pauses the read loop."""
        self.pauseEvent.clear()

    def setPause(self, value: bool = True):
        """Updates the pause/resume state."""
        if value:
            self.pause()
        else:
            self.resume()

    def needAPause(self):
        """It pauses or resume the read loop."""
        self.pauseEvent.wait()

    def preprocess(self, frame) -> np.ndarray:
        """It preprocess the frame."""
        if self.size is not None:
            frame = cv2.resize(frame, self.size[:2])

        if self.flipX:
            frame = cv2.flip(frame, 1)

        if self.flipY:
            frame = cv2.flip(frame, 0)

        return frame

    def process(self, frame: np.ndarray = None) -> np.ndarray:
        """It executes the processing function."""
        if self.hasProcessing():
            kwargs = self.getProcessingParams()
            frame = self.processing(frame, **kwargs)
        return frame

    def update(self):
        """It tries to read a frame from the camera."""
        self.readEvent.clear()
        self.readLock.acquire()

        deviceIsAvailable, frame = self.device.read()

        if deviceIsAvailable:
            frame = self.preprocess(frame)
            self.frame = self.process(frame)

            if self.emitterIsEnabled:
                self.emit("frame-ready", {self.name: self.frame})
                self.emit("frame-available", self.name)

            if self.queueModeEnabled:
                self.queue.put(self.frame)

            # if self.encoderIsEnabled:
            #     self.frame64 = self.encoder.encode(self.frame)

        self.readEvent.set()

        if self.readLock.locked():
            self.readLock.release()

    def read(self, timeout: Union[float, int, None] = 0) -> Union[None, np.ndarray]:
        """It returns a frame or a background.

        Args:
            timeout: max time in seconds to lock operation
        """
        if self.isConnected():
            while self.queueModeEnabled:
                return self.queue.get(timeout=timeout)
            return self.frame
        else:
            return self.background

    def applyDelay(self):
        """If a fps was defined it will wait for respective delay."""
        if self.fps is not None:
            time.sleep(self.delay)

    def run(self):
        """It runs the read loop."""
        self.loadDevice()
        self.running.set()

        while self.running.is_set():
            if self.isConnected():
                self.update()
            else:
                self.reconnect()
            self.applyDelay()
            self.needAPause()

        self.queueModeEnabled = False
        self.readEvent.set()
        self.device.release()

    def getName(self) -> str:
        """It returns the name of the current device."""
        return self.name

    def getFrame(self) -> Union[None, np.ndarray]:
        """It returns the current frame."""
        return self.frame

    def getFrame64(self) -> Union[None, str]:
        """Returns the current frame on base64 format"""
        return self.frame64

    def getBackground(self) -> Union[None, np.ndarray]:
        """It returns the current background."""
        if self.backgroundIsEnabled:
            if self.background is None:
                self.background = self.createBackground(size=self.size)
            return self.background

    def setSpeed(self, fps: Union[int, None] = 10):
        """It updates the frames per second (fps).
        Args:
            fps: frames per sec. If no parameter is passed, auto speed will be set.
        """
        self.fps = fps
        if self.fps is not None:
            self.defaultDelay = 1 / self.fps
            self.delay = self.defaultDelay

    def stop(self):
        """It stops the read loop."""
        self.resume()

        if self.readLock.locked():
            self.readLock.release()

        if self.running.is_set():
            self.running.clear()
            self.thread.join()


class Cameras:
    """A class for manage multiple threaded cameras.
    Args:
        devices: a dict with names and sources of the camera devices.
        reconnectDelay: wait time for try reopen camera device
        fps: frames per second
        verbose: display info messages?
        size: tuple or list with a dimension of the image
        emitterIsEnabled: disable on/emit events (callbacks execution)
        enableBackground: if some error is produced with the camera it will display
                                a black frame with a message.
                                
    Example usage:
        cameras = Cameras(devices={'camera1': {'src': 0}, 'cameras2': {'src': 1}})
    """

    def __init__(self, devices: dict = {}, *args, **kwargs):
        self.devices = {}
        if len(devices) > 0:
            for name, settings in devices.items():
                if isinstance(settings, dict):
                    self.devices[name] = Camera(name=name, **settings)

    def __getitem__(self, name):
        return self.devices[name]

    def __len__(self):
        return len(self.devices)

    def startAll(self):
        """It starts all camera devices on the devices dict."""
        for device in self.devices.values():
            device.start()

    def startOnly(self, deviceName: str = "default"):
        """It starts only a specific device.
        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            device = self.devices[deviceName]
            device.start()

    def stopAll(self):
        """It stops all camera devices."""
        for device in self.devices.values():
            device.stop()

    def stopOnly(self, deviceName: str = "default"):
        """It stops an specific camera device.
        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            device = self.devices[deviceName]
            device.stop()

    def getDevice(self, deviceName: str = "default"):
        """It returns a specific camera device.
        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            return self.devices[deviceName]

    def pauseOnly(self, deviceName="default"):
        """It pauses a specific camera device.
        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            device = self.devices[deviceName]
            device.pause()

    def pauseAll(self):
        """It pauses all camera devices."""
        for device in self.devices.values():
            device.pause()

    def resumeAll(self):
        """It resumes all camera devices."""
        for device in self.devices.values():
            device.resume()

    def resumeOnly(self, deviceName="default"):
        """It resumes a specific camera device.

        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            device = self.devices[deviceName]
            device.resume()

    def setSpeedOnly(self, deviceName: str = "default", fps: int = 10):
        """It updates the FPS captured by a specific devices.

        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            self.devices[deviceName].setSpeed(fps)

    def getAllFrames(self, asDict=True):
        """It returns a list with all cameras current frames.

        Args:
            asDict: return frames as dict or as list?
        """
        if asDict:
            frames = {
                device.getName(): device.getFrame() for device in self.devices.values()
            }
        else:
            frames = [device.getFrame() for device in self.devices.values()]
        return frames

    def getAllFrames64(self, asDict=True):
        """It returns a list with all cameras current frames on base64 format.

        Args:
            asDict: return frames as dict or as list?
        """
        if asDict:
            frames = {
                device.getName(): device.getFrame64()
                for device in self.devices.values()
            }
        else:
            frames = [device.getFrame() for device in self.devices.values()]
        return frames

    def getFrameOf(self, deviceName="default"):
        """It returns a specific frame of a camera device.

        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            return self.devices[deviceName].getFrame()

    def getFrame64Of(self, deviceName="default"):
        """It returns a specific frame on base 64 of a camera device.

        Args:
            deviceName: camera device name.
        """
        if deviceName in self.devices:
            return self.devices[deviceName].getFrame64()

    def read(self, timeout=0, asDict=True):
        """It returns a list or a dict of frames/backgrounds.

        Args:
            timeout: max wait time in seconds.
            asDict: return as a dict?
        """
        if asDict:
            return {
                device.getName(): device.read(timeout=timeout)
                for device in self.devices.values()
            }
        else:
            return [device.read(timeout=timeout) for device in self.devices.values()]

    def on(self, *args, **kwargs):
        for device in self.devices.values():
            device.on(*args, **kwargs)
