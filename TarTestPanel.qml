import QtQuick
import QtQuick.Layouts
import "."  

// Left-hand test bay.
//
// "TEST" used to print one line into the chat and finish, which is
// indistinguishable from nothing happening -- so it got spammed. A test now
// OPENS here and shows the thing itself:
//
//   camera   live frames off /dev/video*, plus any still T.A.R. captured
//   mic      real level bars from tar_meter.py, with a "say something" prompt
//   speaker  SFX buttons you press and confirm you heard
//
// Nothing in here claims success on its own: the mic test waits until it has
// actually seen signal, and the speaker test asks you to confirm.
Item {
    id: root

    // Own palette fallback: when this panel lives in its own window the host's
    // theme object may not exist yet at first paint, and every colour binding
    // threw "Cannot read property of undefined".
    MatugenColors { id: fallbackTheme }
    property var theme: fallbackTheme
    onThemeChanged: if (!theme) theme = fallbackTheme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property string mode: ""            // "camera" | "mic" | "speaker" | "image"
    property string imagePath: ""       // for the image viewer
    property real micLevel: 0.0
    property real micPeak: 0.0
    property bool micHeard: false
    property string statusText: ""
    property bool running: false

    signal closed()
    signal grabFrame()
    signal startMic()
    signal stopMic()
    signal playSfx(string name)

    readonly property string title:
        mode === "camera" ? "CAMERA TEST"
      : mode === "mic"    ? "MICROPHONE TEST"
      : mode === "speaker"? "SPEAKER TEST"
      : mode === "image"  ? "IMAGE"
      : "TEST"

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(root.theme.crust.r, root.theme.crust.g,
                       root.theme.crust.b, 0.82)
        border.width: 1
        border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.4)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.s(12)
        spacing: root.s(10)

        // ---------------------------------------------------------- header
        RowLayout {
            Layout.fillWidth: true
            spacing: root.s(8)
            Text {
                text: root.title
                color: root.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: root.s(11)
                font.letterSpacing: root.s(2)
                font.bold: true
            }
            Item { Layout.fillWidth: true }
            TarHudButton {
                theme: root.theme; accent: root.theme.red; scaleFn: root.scaleFn
                glyph: "\u{f0156}"; label: "CLOSE"
                onClicked: root.closed()
            }
        }
        Rectangle {
            Layout.fillWidth: true; height: 1
            color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.3)
        }

        // ---------------------------------------------------------- camera
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.mode === "camera" || root.mode === "image"

            Rectangle {
                anchors.fill: parent
                color: Qt.rgba(0, 0, 0, 0.55)
                border.width: 1
                border.color: Qt.rgba(root.theme.surface1.r,
                                      root.theme.surface1.g,
                                      root.theme.surface1.b, 0.6)

                Image {
                    id: shot
                    anchors.fill: parent
                    anchors.margins: root.s(3)
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                    // cache-busting suffix: the file keeps the same path while
                    // the frames change, and Image would otherwise never reload
                    source: root.imagePath !== ""
                        ? "file://" + root.imagePath + "?t=" + root.frameTick
                        : ""
                }
                Text {
                    anchors.centerIn: parent
                    visible: shot.status !== Image.Ready
                    text: root.running ? "waiting for a frame…"
                                       : "no frame yet"
                    color: root.theme.overlay1
                    font.family: "JetBrains Mono"
                    font.pixelSize: root.s(10)
                }
                // live indicator
                Row {
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.margins: root.s(7)
                    spacing: root.s(5)
                    visible: root.running && root.mode === "camera"
                    Rectangle {
                        width: root.s(7); height: width; radius: width / 2
                        color: root.theme.red
                        anchors.verticalCenter: parent.verticalCenter
                        SequentialAnimation on opacity {
                            loops: Animation.Infinite; running: root.running
                            NumberAnimation { to: 0.25; duration: 600 }
                            NumberAnimation { to: 1.0; duration: 600 }
                        }
                    }
                    Text {
                        text: "LIVE"
                        color: root.theme.red
                        font.family: "JetBrains Mono"
                        font.pixelSize: root.s(9)
                        font.bold: true
                    }
                }
            }
        }

        // ------------------------------------------------------------- mic
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.mode === "mic"
            spacing: root.s(10)

            Text {
                Layout.fillWidth: true
                text: root.micHeard ? "✓ hearing you"
                    : root.running  ? "say something…"
                                    : "press START and talk"
                color: root.micHeard ? root.theme.green : root.theme.subtext0
                font.family: "JetBrains Mono"
                font.pixelSize: root.s(12)
            }

            // live bars
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: root.s(120)
                color: Qt.rgba(0, 0, 0, 0.4)
                border.width: 1
                border.color: Qt.rgba(root.theme.surface1.r,
                                      root.theme.surface1.g,
                                      root.theme.surface1.b, 0.6)

                Row {
                    anchors.centerIn: parent
                    spacing: root.s(3)
                    Repeater {
                        model: 28
                        Rectangle {
                            width: root.s(6)
                            // a travelling window over the level history so it
                            // reads as a waveform rather than 28 identical bars
                            height: Math.max(root.s(3),
                                root.s(110) * root.barFor(index))
                            anchors.verticalCenter: parent.verticalCenter
                            color: root.micHeard ? root.theme.green : root.accent
                            opacity: 0.55 + 0.45 * root.barFor(index)
                            Behavior on height {
                                NumberAnimation { duration: 70 }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: root.s(8)
                Text {
                    text: "peak"
                    color: root.theme.overlay1
                    font.family: "JetBrains Mono"
                    font.pixelSize: root.s(9)
                }
                Rectangle {
                    Layout.fillWidth: true
                    height: root.s(4)
                    color: Qt.rgba(root.theme.surface1.r, root.theme.surface1.g,
                                   root.theme.surface1.b, 0.7)
                    Rectangle {
                        width: parent.width * Math.min(1, root.micPeak)
                        height: parent.height
                        color: root.micPeak > 0.95 ? root.theme.red : root.accent
                        Behavior on width { NumberAnimation { duration: 90 } }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: root.s(8)
                TarHudButton {
                    theme: root.theme; accent: root.accent; scaleFn: root.scaleFn
                    glyph: root.running ? "\u{f04db}" : "\u{f040a}"
                    label: root.running ? "STOP" : "START"
                    onClicked: root.running ? root.stopMic() : root.startMic()
                }
                Item { Layout.fillWidth: true }
            }
            Item { Layout.fillHeight: true }
        }

        // --------------------------------------------------------- speaker
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.mode === "speaker"
            spacing: root.s(8)

            Text {
                Layout.fillWidth: true
                text: "Press a sound. If you hear nothing, the output device "
                    + "is wrong — change it in /settings."
                color: root.theme.subtext0
                wrapMode: Text.WordWrap
                font.family: "JetBrains Mono"
                font.pixelSize: root.s(10)
            }

            Flow {
                Layout.fillWidth: true
                spacing: root.s(6)
                Repeater {
                    model: ["boot", "ping", "ok", "done", "act",
                            "listen", "open", "close", "warn", "error", "sweep"]
                    delegate: Rectangle {
                        required property string modelData
                        implicitWidth: sfxT.implicitWidth + root.s(18)
                        implicitHeight: root.s(28)
                        color: sfxHov.hovered
                            ? Qt.rgba(root.accent.r, root.accent.g,
                                      root.accent.b, 0.22)
                            : Qt.rgba(root.theme.surface0.r,
                                      root.theme.surface0.g,
                                      root.theme.surface0.b, 0.55)
                        border.width: 1
                        border.color: Qt.rgba(root.accent.r, root.accent.g,
                                              root.accent.b, 0.45)
                        Text {
                            id: sfxT
                            anchors.centerIn: parent
                            text: modelData
                            color: root.theme.text
                            font.family: "JetBrains Mono"
                            font.pixelSize: root.s(10)
                        }
                        HoverHandler { id: sfxHov }
                        TapHandler { onTapped: root.playSfx(modelData) }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: "voice check"
                color: root.theme.overlay1
                font.family: "JetBrains Mono"
                font.pixelSize: root.s(9)
                font.letterSpacing: root.s(1.5)
            }
            TarHudButton {
                theme: root.theme; accent: root.accent; scaleFn: root.scaleFn
                glyph: "\u{f075a}"; label: "SPEAK A LINE"
                onClicked: root.playSfx("__speak__")
            }
            Item { Layout.fillHeight: true }
        }

        // ---------------------------------------------------------- status
        Text {
            Layout.fillWidth: true
            visible: root.statusText !== ""
            text: root.statusText
            color: root.theme.subtext0
            wrapMode: Text.WordWrap
            font.family: "JetBrains Mono"
            font.pixelSize: root.s(10)
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.mode === "camera"
            spacing: root.s(8)
            TarHudButton {
                theme: root.theme; accent: root.accent; scaleFn: root.scaleFn
                glyph: "\u{f0d5d}"; label: "GRAB"
                onClicked: root.grabFrame()
            }
            Item { Layout.fillWidth: true }
        }
    }

    // ---- waveform history -------------------------------------------------
    property int frameTick: 0
    property var levels: []
    function barFor(i) {
        if (!levels || levels.length === 0) return 0.02;
        var idx = levels.length - 1 - (27 - i);
        if (idx < 0 || idx >= levels.length) return 0.02;
        return levels[idx];
    }
    function pushLevel(v) {
        var a = levels.slice();
        a.push(v);
        if (a.length > 28) a = a.slice(a.length - 28);
        levels = a;
    }
}
