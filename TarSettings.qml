import QtQuick
import QtQuick.Layouts
import QtQuick.Effects

// T.A.R. capability + device settings.
//
// Every capability that needs software we don't ship is rendered as a card. If
// no engine is installed the card is covered by a hazard-striped WALL carrying
// the reason and exactly two ways out:
//
//   ASK T.A.R.  -> hands the question to the model, which offers the options
//                  and installs the one you pick  (askInstall)
//   CONNECT     -> no download: find what's already on the box, wire the best
//                  of it, and pick a sane device                (connectCap)
//
// Devices are explicit on purpose: this box has 4 sinks where 3 are silent
// HDMI, so "whatever is default" is not good enough.
Item {
    id: root

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    // data, as handed over by tar_caps.py
    property var caps: ({})          // cap -> {label, blurb, ready, locked, wall, engines, chosen, device_kind}
    property var devices: ({})       // {sinks, sources, cameras, effective, ...}
    property string voice: ""
    property bool speakOn: false

    // live install feedback
    property string busyCap: ""
    property int busyPct: -1
    property string busyLabel: ""

    property var autostart: ({})
    property string startMode: "cinematic"

    signal setAutostart(string key, bool on)
    signal setStartMode(string mode)
    signal askInstall(string cap)
    signal connectCap(string cap)
    signal chooseEngine(string cap, string engine)
    signal chooseDevice(string kind, string id)
    signal testCap(string cap)
    signal toggleSpeak()
    signal refresh()

    property real launchBlockHeight: 0
    readonly property var capOrder: ["tts", "stt", "wakeword", "camera", "vision", "mouse"]

    function devListFor(kind) {
        if (!devices) return [];
        if (kind === "sink") return devices.sinks || [];
        if (kind === "source") return devices.sources || [];
        if (kind === "video") return devices.cameras || [];
        return [];
    }
    function effectiveFor(kind) {
        return devices && devices.effective ? (devices.effective[kind] || "") : "";
    }

    // Reset to the top whenever the pane is shown. It kept its old scroll
    // position, so ON LAUNCH -- which lives at the very top -- was simply off
    // screen every time setup was reopened, and looked like it did not exist.
    onVisibleChanged: if (visible) scroller.contentY = 0
    function toTop() { scroller.contentY = 0 }

    Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: col.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: col
            width: parent.width
            spacing: root.s(14)

            // ------------------------------------------------------- header
            RowLayout {
                Layout.fillWidth: true
                spacing: root.s(10)

                Text {
                    text: "CAPABILITIES"
                    color: root.theme.text
                    font.family: "monospace"
                    font.pixelSize: root.s(13)
                    font.letterSpacing: root.s(3)
                    font.bold: true
                }
                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35)
                }
                TarHudButton {
                    theme: root.theme
                    accent: root.accent
                    scaleFn: root.scaleFn
                    glyph: "\u{f0450}"
                    label: "RESCAN"
                    onClicked: root.refresh()
                }
            }

            // ----------------------------------------------------- on launch
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: startCol.implicitHeight + root.s(22)
                onHeightChanged: root.launchBlockHeight = height
                color: Qt.rgba(root.theme.mantle.r, root.theme.mantle.g,
                               root.theme.mantle.b, 0.45)
                border.width: 1
                border.color: Qt.rgba(root.accent.r, root.accent.g,
                                      root.accent.b, 0.3)

                ColumnLayout {
                    id: startCol
                    anchors.fill: parent
                    anchors.margins: root.s(11)
                    spacing: root.s(8)

                    Text {
                        text: "ON LAUNCH"
                        color: root.theme.text
                        font.family: "monospace"
                        font.pixelSize: root.s(11)
                        font.letterSpacing: root.s(2)
                        font.bold: true
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "What T.A.R. switches on by itself when it starts."
                        color: root.theme.subtext0
                        font.family: "monospace"
                        font.pixelSize: root.s(10)
                        wrapMode: Text.WordWrap
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: root.s(6)
                        Repeater {
                            model: [
                                { k: "speak", t: "speak replies" },
                                { k: "mic",   t: "mic on" },
                                { k: "wake",  t: "wake word" },
                                { k: "wake_listen", t: "listen after \"hey tar\"" },
                                { k: "warm",  t: "preload model" },
                                { k: "sfx",   t: "ui sounds" },
                                { k: "greet", t: "greet me" }
                            ]
                            delegate: Rectangle {
                                required property var modelData
                                // wake_listen defaults ON (missing = on)
                                readonly property bool on:
                                    root.autostart && (modelData.k === "wake_listen"
                                        ? root.autostart[modelData.k] !== false
                                        : root.autostart[modelData.k] === true)
                                implicitWidth: asT.implicitWidth + root.s(20)
                                implicitHeight: root.s(24)
                                color: on ? Qt.rgba(root.accent.r, root.accent.g,
                                                    root.accent.b, 0.22)
                                          : Qt.rgba(root.theme.surface0.r,
                                                    root.theme.surface0.g,
                                                    root.theme.surface0.b, 0.5)
                                border.width: 1
                                border.color: on
                                    ? Qt.rgba(root.accent.r, root.accent.g,
                                              root.accent.b, 0.6)
                                    : Qt.rgba(root.theme.surface1.r,
                                              root.theme.surface1.g,
                                              root.theme.surface1.b, 0.5)
                                Text {
                                    id: asT
                                    anchors.centerIn: parent
                                    text: (on ? "\u25cf  " : "\u25cb  ")
                                          + modelData.t
                                    color: on ? root.theme.text
                                              : root.theme.subtext1
                                    font.family: "monospace"
                                    font.pixelSize: root.s(10)
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.setAutostart(modelData.k,
                                                                 !parent.on)
                                }
                            }
                        }
                    }

                    Text {
                        text: "WINDOW MODE"
                        color: root.theme.overlay1
                        font.family: "monospace"
                        font.pixelSize: root.s(9)
                        font.letterSpacing: root.s(1.5)
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: root.s(6)
                        Repeater {
                            model: ["window", "cinematic", "floating"]
                            delegate: Rectangle {
                                required property string modelData
                                readonly property bool on:
                                    root.startMode === modelData
                                implicitWidth: smT.implicitWidth + root.s(18)
                                implicitHeight: root.s(22)
                                color: on ? Qt.rgba(root.accent.r, root.accent.g,
                                                    root.accent.b, 0.2)
                                          : Qt.rgba(root.theme.surface0.r,
                                                    root.theme.surface0.g,
                                                    root.theme.surface0.b, 0.45)
                                border.width: 1
                                border.color: on
                                    ? Qt.rgba(root.accent.r, root.accent.g,
                                              root.accent.b, 0.6)
                                    : Qt.rgba(root.theme.surface1.r,
                                              root.theme.surface1.g,
                                              root.theme.surface1.b, 0.45)
                                Text {
                                    id: smT
                                    anchors.centerIn: parent
                                    text: modelData
                                    color: on ? root.theme.text
                                              : root.theme.subtext1
                                    font.family: "monospace"
                                    font.pixelSize: root.s(10)
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.setStartMode(modelData)
                                }
                            }
                        }
                    }
                }
            }

            // ------------------------------------------------- capability cards
            Repeater {
                model: root.capOrder

                delegate: Item {
                    id: card
                    required property string modelData
                    readonly property var cap: root.caps ? root.caps[modelData] : undefined
                    readonly property bool locked: cap ? !!cap.locked : true
                    readonly property bool installing: root.busyCap === modelData

                    visible: !!cap
                    Layout.fillWidth: true
                    implicitHeight: visible ? body.implicitHeight + root.s(22) : 0

                    // frame
                    Rectangle {
                        anchors.fill: parent
                        color: Qt.rgba(root.theme.mantle.r, root.theme.mantle.g,
                                       root.theme.mantle.b, 0.45)
                        border.width: 1
                        border.color: card.locked
                            ? Qt.rgba(root.theme.overlay0.r, root.theme.overlay0.g,
                                      root.theme.overlay0.b, 0.45)
                            : Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.4)
                        Behavior on border.color { ColorAnimation { duration: 180 } }
                    }

                    ColumnLayout {
                        id: body
                        anchors.fill: parent
                        anchors.margins: root.s(11)
                        spacing: root.s(8)

                        // ---- title line
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: root.s(9)

                            Text {
                                text: card.cap ? (card.cap.icon || "\u{f0e8}") : ""
                                color: card.locked ? root.theme.overlay1 : root.accent
                                font.family: "monospace"
                                font.pixelSize: root.s(18)
                            }
                            Text {
                                text: card.cap ? card.cap.label.toUpperCase() : ""
                                color: root.theme.text
                                font.family: "monospace"
                                font.pixelSize: root.s(12)
                                font.letterSpacing: root.s(1.5)
                                font.bold: true
                            }
                            // status pill
                            Rectangle {
                                implicitWidth: st.implicitWidth + root.s(12)
                                implicitHeight: root.s(18)
                                radius: root.s(2)
                                color: card.locked
                                    ? Qt.rgba(root.theme.red.r, root.theme.red.g,
                                              root.theme.red.b, 0.16)
                                    : Qt.rgba(root.theme.green.r, root.theme.green.g,
                                              root.theme.green.b, 0.16)
                                Text {
                                    id: st
                                    anchors.centerIn: parent
                                    text: card.locked ? "LOCKED" : "READY"
                                    color: card.locked ? root.theme.red : root.theme.green
                                    font.family: "monospace"
                                    font.pixelSize: root.s(9)
                                    font.letterSpacing: root.s(1)
                                    font.bold: true
                                }
                            }
                            Item { Layout.fillWidth: true }

                            TarHudButton {
                                visible: !card.locked
                                theme: root.theme
                                accent: root.accent
                                scaleFn: root.scaleFn
                                glyph: "\u{f04d1}"
                                label: "TEST"
                                onClicked: root.testCap(card.modelData)
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: card.cap ? card.cap.blurb : ""
                            color: root.theme.subtext0
                            font.family: "monospace"
                            font.pixelSize: root.s(10)
                            wrapMode: Text.WordWrap
                        }

                        // ---- engine chips (only meaningful once unlocked)
                        Flow {
                            Layout.fillWidth: true
                            spacing: root.s(6)
                            visible: !card.locked

                            Repeater {
                                model: card.cap && card.cap.engines ? card.cap.engines : []
                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property bool isChosen:
                                        card.cap && card.cap.chosen === modelData.engine
                                    implicitWidth: en.implicitWidth + root.s(16)
                                    implicitHeight: root.s(22)
                                    color: isChosen
                                        ? Qt.rgba(root.accent.r, root.accent.g,
                                                  root.accent.b, 0.20)
                                        : Qt.rgba(root.theme.surface0.r,
                                                  root.theme.surface0.g,
                                                  root.theme.surface0.b, 0.5)
                                    border.width: 1
                                    border.color: isChosen
                                        ? Qt.rgba(root.accent.r, root.accent.g,
                                                  root.accent.b, 0.65)
                                        : Qt.rgba(root.theme.surface1.r,
                                                  root.theme.surface1.g,
                                                  root.theme.surface1.b, 0.6)
                                    opacity: modelData.ready ? 1.0 : 0.45

                                    Text {
                                        id: en
                                        anchors.centerIn: parent
                                        text: modelData.engine
                                            + (modelData.ready ? "" : " ·not installed")
                                        color: isChosen ? root.theme.text : root.theme.subtext1
                                        font.family: "monospace"
                                        font.pixelSize: root.s(10)
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: modelData.ready
                                            ? root.chooseEngine(card.modelData,
                                                                modelData.engine)
                                            : root.askInstall(card.modelData)
                                    }
                                }
                            }
                        }

                        // ---- device picker
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: root.s(4)
                            visible: !card.locked && card.cap
                                     && !!card.cap.device_kind

                            Text {
                                text: card.cap && card.cap.device_kind === "source"
                                        ? "INPUT DEVICE"
                                        : card.cap && card.cap.device_kind === "video"
                                            ? "CAMERA" : "OUTPUT DEVICE"
                                color: root.theme.overlay1
                                font.family: "monospace"
                                font.pixelSize: root.s(9)
                                font.letterSpacing: root.s(1.5)
                            }

                            Flow {
                                Layout.fillWidth: true
                                spacing: root.s(5)

                                Repeater {
                                    model: card.cap
                                        ? root.devListFor(card.cap.device_kind) : []
                                    delegate: Rectangle {
                                        required property var modelData
                                        readonly property string kind:
                                            card.cap ? card.cap.device_kind : ""
                                        // a monitor source is not a microphone
                                        readonly property bool bad:
                                            !!modelData.monitor
                                        readonly property bool risky:
                                            !!modelData.hdmi
                                        readonly property bool isOn:
                                            root.effectiveFor(kind) === modelData.id

                                        implicitWidth: dn.implicitWidth + root.s(16)
                                        implicitHeight: root.s(22)
                                        color: isOn
                                            ? Qt.rgba(root.accent.r, root.accent.g,
                                                      root.accent.b, 0.20)
                                            : Qt.rgba(root.theme.surface0.r,
                                                      root.theme.surface0.g,
                                                      root.theme.surface0.b, 0.45)
                                        border.width: 1
                                        border.color: isOn
                                            ? Qt.rgba(root.accent.r, root.accent.g,
                                                      root.accent.b, 0.65)
                                            : Qt.rgba(root.theme.surface1.r,
                                                      root.theme.surface1.g,
                                                      root.theme.surface1.b, 0.5)
                                        opacity: bad ? 0.35 : 1.0

                                        Text {
                                            id: dn
                                            anchors.centerIn: parent
                                            text: (modelData.name || modelData.id)
                                                + (risky ? " ·hdmi" : "")
                                                + (bad ? " ·loopback" : "")
                                            color: isOn ? root.theme.text
                                                        : root.theme.subtext1
                                            font.family: "monospace"
                                            font.pixelSize: root.s(10)
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            enabled: !bad
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: root.chooseDevice(kind,
                                                                         modelData.id)
                                        }
                                    }
                                }
                            }
                        }

                        // ---- speak toggle lives with the TTS card
                        RowLayout {
                            Layout.fillWidth: true
                            visible: card.modelData === "tts" && !card.locked
                            spacing: root.s(8)

                            TarHudButton {
                                theme: root.theme
                                accent: root.accent
                                scaleFn: root.scaleFn
                                glyph: root.speakOn ? "\u{f075a}" : "\u{f075f}"
                                label: root.speakOn ? "SPEAKING" : "MUTED"
                                active: root.speakOn
                                onClicked: root.toggleSpeak()
                            }
                            Text {
                                visible: root.voice !== ""
                                text: "voice: " + root.voice
                                color: root.theme.subtext0
                                font.family: "monospace"
                                font.pixelSize: root.s(10)
                            }
                        }
                    }

                    // ------------------------------------------------- THE WALL
                    Rectangle {
                        anchors.fill: parent
                        visible: card.locked
                        color: Qt.rgba(root.theme.crust.r, root.theme.crust.g,
                                       root.theme.crust.b, 0.88)

                        // hazard stripes
                        Item {
                            anchors.fill: parent
                            clip: true
                            opacity: 0.07
                            Repeater {
                                model: 26
                                Rectangle {
                                    width: root.s(11)
                                    height: parent.height * 3
                                    y: -parent.height
                                    x: index * root.s(30) - parent.height
                                    color: root.theme.yellow
                                    rotation: 34
                                    transformOrigin: Item.TopLeft
                                }
                            }
                        }

                        ColumnLayout {
                            anchors.centerIn: parent
                            width: parent.width - root.s(26)
                            spacing: root.s(8)

                            RowLayout {
                                Layout.alignment: Qt.AlignHCenter
                                spacing: root.s(8)
                                Text {
                                    text: "\u{f033e}"     // padlock
                                    color: root.theme.yellow
                                    font.family: "monospace"
                                    font.pixelSize: root.s(16)
                                }
                                Text {
                                    text: card.cap
                                        ? (card.cap.wall || "unavailable").toUpperCase()
                                        : ""
                                    color: root.theme.yellow
                                    font.family: "monospace"
                                    font.pixelSize: root.s(11)
                                    font.letterSpacing: root.s(1.5)
                                    font.bold: true
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: card.cap && card.cap.requires_unmet
                                      && card.cap.requires_unmet.length
                                    ? "unlock that first — this is built on top of it"
                                    : card.cap ? card.cap.label
                                        + " needs an engine before it can be used."
                                        : ""
                                color: root.theme.subtext0
                                font.family: "monospace"
                                font.pixelSize: root.s(10)
                                wrapMode: Text.WordWrap
                            }

                            // install progress, when a pull/download is running
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: card.installing && root.busyPct >= 0
                                spacing: root.s(3)
                                Text {
                                    text: root.busyLabel + "  " + root.busyPct + "%"
                                    color: root.accent
                                    font.family: "monospace"
                                    font.pixelSize: root.s(10)
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    height: root.s(3)
                                    color: Qt.rgba(root.theme.surface1.r,
                                                   root.theme.surface1.g,
                                                   root.theme.surface1.b, 0.7)
                                    Rectangle {
                                        width: parent.width
                                               * Math.max(0, Math.min(100, root.busyPct))
                                               / 100
                                        height: parent.height
                                        color: root.accent
                                        Behavior on width {
                                            NumberAnimation { duration: 180 }
                                        }
                                    }
                                }
                            }

                            // the two ways out
                            RowLayout {
                                Layout.alignment: Qt.AlignHCenter
                                spacing: root.s(10)
                                visible: !card.installing

                                TarHudButton {
                                    theme: root.theme
                                    accent: root.theme.yellow
                                    scaleFn: root.scaleFn
                                    glyph: "\u{f02fd}"
                                    label: "ASK T.A.R."
                                    onClicked: root.askInstall(card.modelData)
                                }
                                TarHudButton {
                                    theme: root.theme
                                    accent: root.accent
                                    scaleFn: root.scaleFn
                                    glyph: "\u{f0337}"
                                    label: "CONNECT"
                                    onClicked: root.connectCap(card.modelData)
                                }
                            }

                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                visible: card.installing
                                text: "working…"
                                color: root.accent
                                font.family: "monospace"
                                font.pixelSize: root.s(10)
                            }

                            Text {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: "ASK lets you choose · CONNECT uses what's "
                                      + "already installed"
                                color: root.theme.overlay0
                                font.family: "monospace"
                                font.pixelSize: root.s(9)
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: root.s(6) }
        }
    }
}
