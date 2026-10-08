import QtQuick
import QtMultimedia
import Quickshell
import "."

// "What T.A.R. sees". Two modes:
//   still  -- the last thing it looked at: camera photo, your screen, or the
//             spot it clicked (with a crosshair)
//   live   -- your webcam, streamed right here (Qt camera, no app, no photo).
//             While live, a frame is saved every second so "am I smiling?"
//             answers from the live view instead of grabbing the camera again.
TarDeck {
    id: viewer

    property string path: ""
    property string source: ""          // camera | screen | click | image
    property string caption: ""
    property real shownAt: 0
    property bool live: false           // set by the host only
    signal liveToggled(bool on)
    property string cameraId: ""        // e.g. /dev/video0 (setup's choice)
    property string frameFile: ""       // where live frames are saved

    glyph: live ? "\u{f0567}" : "\u{f0208}"
    title: "T.A.R. SEES"
    status: live ? "LIVE CAMERA"
          : source === "camera" ? "camera photo"
          : source === "click" ? "where it clicked"
          : source === "screen" ? "your screen"
          : path ? "image" : "nothing yet"
    statusColor: live ? theme.red : theme.subtext0
    statusPulse: live

    // ---------------------------------------------------------------- camera
    MediaDevices { id: devs }
    // The camera exists ONLY while the live view is on. An inactive Camera can
    // keep /dev/video0 open, and T.A.R.'s own photo grab then failed with
    // "camera busy" right after the live view was switched off.
    Loader {
        id: camLoader
        active: viewer.live && viewer.open
        sourceComponent: CaptureSession {
            camera: Camera {
                active: true
                cameraDevice: {
                    var list = devs.videoInputs;
                    for (var i = 0; i < list.length; i++)
                        if (viewer.cameraId && String(list[i].id).indexOf(viewer.cameraId) >= 0)
                            return list[i];
                    return devs.defaultVideoInput;
                }
            }
            videoOutput: liveOut
        }
    }
    readonly property string camError: camLoader.item && camLoader.item.camera
                                        ? camLoader.item.camera.errorString : ""
    Timer {                                 // live frame for camera_look
        interval: 1000; repeat: true
        running: viewer.live && viewer.open && viewer.frameFile !== ""
        onTriggered: liveOut.grabToImage(r => r.saveToFile(viewer.frameFile))
    }

    Column {
        anchors.fill: parent
        spacing: viewer.s(12)

        // the picture: 16:9, full width
        Rectangle {
            id: frameBox
            width: parent.width
            height: width * 9 / 16
            radius: viewer.s(4)
            color: Qt.rgba(0, 0, 0, 0.55)
            border.width: 1
            border.color: viewer.live ? Qt.rgba(viewer.theme.red.r, viewer.theme.red.g, viewer.theme.red.b, 0.7)
                                      : Qt.rgba(viewer.accent.r, viewer.accent.g, viewer.accent.b, 0.35)
            clip: true

            VideoOutput {
                id: liveOut
                anchors.fill: parent
                anchors.margins: viewer.s(2)
                visible: viewer.live
                fillMode: VideoOutput.PreserveAspectFit
            }
            Image {
                anchors.fill: parent
                anchors.margins: viewer.s(2)
                visible: !viewer.live && viewer.path !== ""
                source: viewer.path ? "file://" + viewer.path : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
                smooth: true
            }
            Text {
                anchors.centerIn: parent
                visible: !viewer.live && viewer.path === ""
                text: "nothing to show yet"
                color: viewer.theme.overlay0
                font.family: "JetBrains Mono"
                font.pixelSize: viewer.s(11)
            }
            Rectangle {                     // LIVE badge
                visible: viewer.live
                anchors.top: parent.top; anchors.left: parent.left
                anchors.margins: viewer.s(8)
                width: liveTxt.implicitWidth + viewer.s(14); height: viewer.s(20)
                radius: viewer.s(3)
                color: Qt.rgba(0, 0, 0, 0.6)
                Text {
                    id: liveTxt
                    anchors.centerIn: parent
                    text: "● LIVE"
                    color: viewer.theme.red
                    font.family: "JetBrains Mono"; font.pixelSize: viewer.s(10); font.bold: true
                }
            }
            Rectangle {                     // flash when a new picture lands
                id: flash
                anchors.fill: parent
                color: viewer.accent
                opacity: 0
            }
        }

        // caption
        Text {
            width: parent.width
            visible: !viewer.live && viewer.caption !== ""
            text: viewer.caption
            color: viewer.theme.text
            font.family: "JetBrains Mono"
            font.pixelSize: viewer.s(11)
            wrapMode: Text.Wrap
        }
        Text {
            visible: !viewer.live && viewer.shownAt > 0
            text: "seen at " + Qt.formatTime(new Date(viewer.shownAt), "hh:mm:ss")
            color: viewer.theme.overlay1
            font.family: "JetBrains Mono"
            font.pixelSize: viewer.s(9)
        }
        Text {
            width: parent.width
            visible: viewer.live
            text: viewer.camError !== "" ? "camera error: " + viewer.camError
                : "streaming your webcam -- ask \"am I smiling?\" or \"what am I holding?\""
            color: viewer.camError !== "" ? viewer.theme.red : viewer.theme.subtext0
            font.family: "JetBrains Mono"
            font.pixelSize: viewer.s(10)
            wrapMode: Text.Wrap
        }

        Row {
            spacing: viewer.s(8)
            TarHudButton {
                theme: viewer.theme; accent: viewer.live ? viewer.theme.red : viewer.accent
                scaleFn: viewer.scaleFn
                glyph: viewer.live ? "\u{f0568}" : "\u{f0567}"
                label: viewer.live ? "STOP CAMERA" : "LIVE CAMERA"
                active: viewer.live
                onClicked: viewer.liveToggled(!viewer.live)
            }
            TarHudButton {
                visible: !viewer.live && viewer.path !== ""
                theme: viewer.theme; accent: viewer.accent; scaleFn: viewer.scaleFn
                glyph: "\u{f0b4b}"; label: "OPEN FILE"
                onClicked: Quickshell.execDetached(["xdg-open", viewer.path])
            }
        }
    }

    onShownAtChanged: flashAnim.restart()
    SequentialAnimation {
        id: flashAnim
        NumberAnimation { target: flash; property: "opacity"; to: 0.25; duration: 60 }
        NumberAnimation { target: flash; property: "opacity"; to: 0; duration: 420 }
    }
}
