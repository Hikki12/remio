"""Example experiment with GUI."""
import os
import sys
from PyQt5 import uic
from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QTimer
from remio import Mockup
from widgets import QImageLabel
from routes import *
from settings import (
    serverSettings,
    streamSettings,
    cameraSettings,
    serialSettings,
)
from timers import PausableTimer
from variables import Variables


ui_path = os.path.dirname(os.path.abspath(__file__))
ui_file = os.path.join(ui_path, "gui.ui")


MOCKUP_ROOM = "room-x"


class CustomMockup(QMainWindow, Mockup):
    """A class for manage a mockup with a local GUI."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        uic.loadUi("gui.ui", self)
        self.configureGUI()
        self.configureControlButtons()
        self.configureSerial()
        self.configureSocket()
        self.configureTimers()
        self.configureVariables()

    # Configurations
    def configureGUI(self):
        """Configures buttons events."""
        self.image = QImageLabel(self.qimage)
        self.pauseBtn.clicked.connect(lambda value: self.updateVideoPauseState(value))
        self.streamBtn.clicked.connect(lambda value: self.streamer.setPause(value))
        self.ledSerial.clicked.connect(lambda value: self.serialReconnect(value))
        self.ledSocket.clicked.connect(lambda value: self.socketReconnect(value))

    def configureControlButtons(self):
        """Configures the control buttons."""
        self.btn1.clicked.connect(lambda value: self.updateVariables("btn1", value))
        self.btn2.clicked.connect(lambda value: self.updateVariables("btn2", value))
        self.btn3.clicked.connect(lambda value: self.updateVariables("btn3", value))

    def configureSerial(self):
        """Configures serial on/emit events."""
        self.serial.on("connection", self.serialConnectionStatus)
        self.serial.on("ports", self.serialPortsUpdate)
        self.serial.on("data", self.serialDataIncoming)
        self.serialPortsUpdate(self.serial.ports())

    def configureSocket(self):
        """Configures socket on/emit events."""
        self.socket.on("connection", self.socketConnectionStatus)
        self.socket.on(SERVER_SENDS_DATA_EXPERIMENT, self.receiveVariables)
        self.socket.on(
            SERVER_NOTIFIES_DATA_WERE_RECEIVED_EXPERIMENT, self.streamVariablesOK
        )
        self.socket.on(
            SERVER_REQUESTS_DATA_EXPERIMENT, lambda: self.streamVariables(lock=False)
        )
        self.socket.on(
            SERVER_STREAMER_SET_PAUSE_EXPERIMENT,
            lambda pause: self.updateVideoPauseState(pause),
        )

    def configureTimers(self):
        """Configures some timers."""
        self.videoTimer = QTimer()
        self.videoTimer.timeout.connect(self.updateVideo)
        self.videoTimer.start(1000 // 15)  # 1000 // FPS
        self.variablesTimer = PausableTimer(3, self.superviseVariablesStreaming)

    def configureVariables(self):
        """Configures control variables."""
        self.variables = Variables(
            {
                "btn1": False,
                "btn2": False,
                "btn3": False,
            }
        )

    # GUI
    def lockGUI(self):
        """Locks the GUI elements."""
        self.btn1.setEnabled(False)
        self.btn2.setEnabled(False)
        self.btn3.setEnabled(False)

    def unlockGUI(self):
        """Unlocks the GUI elements."""
        self.btn1.setEnabled(True)
        self.btn2.setEnabled(True)
        self.btn3.setEnabled(True)

    def setVariablesOnGUI(self):
        """Sets variables on the GUI."""
        variables = self.variables.values()
        self.btn1.setChecked(variables["btn1"])
        self.btn2.setChecked(variables["btn2"])
        self.btn3.setChecked(variables["btn3"])

    # Serial
    def serialPortsUpdate(self, ports: list):
        """Sends to the server the list of serial devices."""
        event = {"serial": {"ports": ports}}
        self.socket.emit(EXPERIMENT_EMITS_EVENT_SERVER, event)
        self.devices.clear()
        self.devices.addItems(ports)

    def serialConnectionStatus(self, status: dict = {"arduino": False}):
        """Sends to the server the serial devices connection status."""
        self.ledSerial.setChecked(status.get("arduino", False))

    def serialDataIncoming(self, data: str):
        """Reads incoming data from the serial device."""
        message = data["arduino"]
        if "$" in message:
            print("message: ", message)
        else:
            self.variables.update(message)
            self.setVariablesOnGUI()
            self.variables.setUpdated(False)
            self.streamVariables()

    def serialReconnect(self, value: bool):
        """Updates the serial port."""
        if value:
            self.serial["arduino"].setPort(self.devices.currentText())
        else:
            self.serial["arduino"].disconnect()
        self.ledSerial.setChecked(self.serial["arduino"].isConnected())

    # SocketIO
    def socketConnectionStatus(self):
        """Shows the connection socket status."""
        status = self.socket.isConnected()
        self.ledSocket.setChecked(status)
        if status:
            self.socket.emit(EXPERIMENT_JOINS_ROOM_SERVER, MOCKUP_ROOM)

    def socketReconnect(self, value: bool = True):
        """Updates the socketio connection."""
        self.socket.toogle(value)
        self.ledSocket.setChecked(self.socket.isConnected())

    # Video
    def updateVideo(self):
        """Updates video image."""
        image = self.camera.getFrameOf("webcam")
        self.image.setImage(image, 400, 300)
        self.streamer.stream({"webcam": image})

    def updateVideoPauseState(self, status: bool):
        """Updates video pause status."""
        self.camera["webcam"].setPause(status)
        self.streamer.setPause(status)

    def superviseVariablesStreaming(self):
        """Checks the variables updated status and restores the backup if necessary.`"""
        # If variables not reached the web then restore the backup
        if not self.variables.updated():
            self.variables.restore()
            self.setVariablesOnGUI()

        # Reset updated variables status and unlock the GUI
        self.variables.setUpdated(False)
        self.variablesTimer.pause(reset=True)
        self.unlockGUI()

    # Variables
    def receiveVariables(self, data: dict = {}):
        """Receives variables coming from the server."""
        self.variables.update(data)
        self.setVariablesOnGUI()

        # Say to the server the data were received (OK)
        self.socket.emit(EXPERIMENT_NOTIFIES_DATA_WERE_RECEIVED_SERVER)

    def updateVariables(self, key: str, value=None):
        """Updates variables values and streams they to the server."""
        # Set a new single variable value
        self.variables.set(key, value)
        self.variables.setUpdated(False)

        # Send changes to the serial device
        self.serial["arduino"].write(self.variables.json())

        self.streamVariables()

    def streamVariables(self, lock: bool = True):
        """Streams variables to the server."""
        # Send changes to the server
        self.socket.emit(EXPERIMENT_SENDS_DATA_SERVER, self.variables.json())

        # Lock the GUI and wait for a response
        if lock:
            self.lockGUI()
            self.variablesTimer.resume(now=False)

    def streamVariablesOK(self):
        """It's called when the server notifies variables were received correctly."""
        self.variables.setUpdated(True)
        self.variablesTimer.resume(now=True)

    def closeEvent(self, event):
        """Stops running threads/processes when close the window."""
        self.stop()
        self.variablesTimer.stop()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    experiment = CustomMockup(
        serverSettings=serverSettings,
        streamSettings=streamSettings,
        cameraSettings=cameraSettings,
        serialSettings=serialSettings,
    )
    experiment.start(
        camera=True,
        serial=False,
        socket=True,
        streamer=False,  # disable automatic streaming
        wait=False,
    )
    experiment.show()
    sys.exit(app.exec_())
