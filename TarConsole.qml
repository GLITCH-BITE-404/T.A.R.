import QtQuick
import QtQuick.Controls
import "."

// T.A.R. console -- the slide-out settings rig. Replaces dumping "/models"
// as a wall of text: everything here is a live control that writes straight
// through to the brain's config.
Item {
    id: con

    property var theme
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }
    property color accent: "#cba6f7"

    property bool open: false

    // data, owned by the host
    property var modelsModel: null
    property var memoryModel: null

    property string activeModel: ""
    property string activeTier: ""
    property string activeLane: "open"
    property bool autotier: true
    property bool micEnabled: false
    property bool speakReplies: false
    property real volume: 0.7
    property string hwNote: ""
    property bool cloudOn: false          // cloud brain is what answers
    property string cloudModel: ""
    property string tab: "local"          // "local" | "cloud"

    signal pinModel(string name)
    signal setLane(string lane)
    signal setAuto()
    signal forgetFact(string text)
    signal refreshRequested()
    signal micToggled(bool on)
    signal speakToggled(bool on)
    signal volumeRequested(real v)
    signal undoRequested()
    signal closed()
    signal setBackend(string which)       // "claude" (cloud) | "local"
    signal setKey(string key)

    onOpenChanged: if (open) { tab = cloudOn ? "cloud" : "local"; refreshRequested() }

    // Host anchors this to the OUTSIDE edge of the panel. Closed, it tucks
    // back behind the panel; open, it slides out to the side. It never covers
    // the console it belongs to.
    width: con.s(330)

    transform: Translate {
        x: con.open ? 0 : -con.width - con.s(14)
        Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutExpo } }
    }
    opacity: open ? 1.0 : 0.0
    visible: opacity > 0.01
    Behavior on opacity { NumberAnimation { duration: 200 } }
    z: -1

    // ---------------------------------------------------------------- chrome
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(con.theme.crust.r, con.theme.crust.g, con.theme.crust.b, 0.93)
    }
    Rectangle {
        anchors.left: parent.left
        width: 1; height: parent.height
        color: Qt.rgba(con.accent.r, con.accent.g, con.accent.b, 0.45)
    }
    // scan sweep down the left seam
    Rectangle {
        anchors.left: parent.left
        width: 2; height: con.s(70)
        color: con.accent
        opacity: 0.8
        SequentialAnimation on y {
            loops: Animation.Infinite; running: con.open
            NumberAnimation { from: -con.s(70); to: con.height
                              duration: 5200; easing.type: Easing.InOutSine }
            PauseAnimation { duration: 1400 }
        }
    }

    // ---------------------------------------------------------------- content
    Column {
        id: head
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: con.s(16)
        spacing: con.s(3)

        Row {
            spacing: con.s(8)
            Text {
                text: "◢ TAR//CONSOLE"
                color: con.accent
                font.family: "JetBrains Mono"
                font.pixelSize: con.s(13)
                font.bold: true
                font.letterSpacing: con.s(1.5)
            }
            Text {
                text: "▊"
                color: con.accent
                font.family: "JetBrains Mono"
                font.pixelSize: con.s(13)
                SequentialAnimation on opacity {
                    loops: Animation.Infinite; running: con.open
                    NumberAnimation { to: 0.0; duration: 520 }
                    NumberAnimation { to: 1.0; duration: 520 }
                }
            }
        }
        Text {
            text: con.hwNote
            color: con.theme.overlay0
            font.family: "JetBrains Mono"
            font.pixelSize: con.s(9)
            width: head.width
            elide: Text.ElideRight
        }
    }

    Rectangle {
        id: headRule
        anchors.top: head.bottom
        anchors.topMargin: con.s(10)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: con.s(16)
        anchors.rightMargin: con.s(16)
        height: 1
        color: Qt.rgba(con.theme.surface1.r, con.theme.surface1.g,
                       con.theme.surface1.b, 0.6)
    }

    // ------------------------------------------------------------------ tabs
    Row {
        id: tabs
        anchors.top: headRule.bottom
        anchors.topMargin: con.s(10)
        anchors.left: parent.left
        anchors.leftMargin: con.s(16)
        spacing: con.s(7)
        TarConsoleChip {
            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
            text: "LOCAL"
            active: con.tab === "local"
            onTapped: con.tab = "local"
        }
        TarConsoleChip {
            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
            text: con.cloudOn ? "CLOUD ●" : "CLOUD"
            active: con.tab === "cloud"
            onTapped: con.tab = "cloud"
        }
    }

    Flickable {
        anchors.top: tabs.bottom
        anchors.topMargin: con.s(12)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: con.s(16)
        anchors.rightMargin: con.s(16)
        anchors.bottomMargin: con.s(52)
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
            id: body
            width: parent.width
            spacing: con.s(16)

            // ====================================================== RUNTIME
            TarConsoleSection {
                width: body.width
                visible: con.tab === "local"
                theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                title: "RUNTIME"

                Row {
                    spacing: con.s(7)
                    TarConsoleChip {
                        theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                        text: "AUTO"
                        active: con.autotier
                        onTapped: con.setAuto()
                    }
                    TarConsoleChip {
                        theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                        text: "UNRESTRICTED"
                        active: con.activeLane === "open"
                        onTapped: con.setLane("open")
                    }
                    TarConsoleChip {
                        theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                        text: "STOCK"
                        active: con.activeLane === "safe"
                        onTapped: con.setLane("safe")
                    }
                }
            }

            // ================================================== CLOUD BRAIN
            TarConsoleSection {
                width: body.width
                visible: con.tab === "cloud"
                theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                title: "CLOUD BRAIN"
                note: con.cloudOn ? "answering via the API" : "local brain answering"

                Column {
                    width: parent.width
                    spacing: con.s(8)

                    Row {
                        spacing: con.s(7)
                        TarConsoleChip {
                            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                            text: "ON"
                            active: con.cloudOn
                            onTapped: con.setBackend("claude")
                        }
                        TarConsoleChip {
                            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                            text: "OFF"
                            active: !con.cloudOn
                            onTapped: con.setBackend("local")
                        }
                    }

                    Text {
                        width: parent.width
                        text: con.cloudOn
                            ? "> " + con.cloudModel
                            : "pick a model below or tap ON"
                        color: con.cloudOn ? con.theme.text : con.theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: con.s(10)
                        elide: Text.ElideRight
                    }

                    // paste a key: Gemini (free) or Anthropic (sk-ant-...)
                    Rectangle {
                        width: parent.width
                        height: con.s(26)
                        color: "transparent"
                        border.width: 1
                        border.color: keyIn.activeFocus
                            ? Qt.rgba(con.accent.r, con.accent.g, con.accent.b, 0.7)
                            : Qt.rgba(con.theme.surface1.r, con.theme.surface1.g,
                                      con.theme.surface1.b, 0.6)
                        TextInput {
                            id: keyIn
                            anchors.fill: parent
                            anchors.leftMargin: con.s(8)
                            anchors.rightMargin: con.s(8)
                            verticalAlignment: TextInput.AlignVCenter
                            echoMode: TextInput.Password
                            color: con.theme.text
                            font.family: "JetBrains Mono"
                            font.pixelSize: con.s(10)
                            clip: true
                            onAccepted: {
                                if (text.trim() !== "") con.setKey(text.trim());
                                text = "";
                            }
                        }
                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: con.s(8)
                            anchors.verticalCenter: parent.verticalCenter
                            visible: keyIn.text === "" && !keyIn.activeFocus
                            text: "paste API key + enter (gemini or sk-ant-)"
                            color: con.theme.overlay0
                            font.family: "JetBrains Mono"
                            font.pixelSize: con.s(9)
                        }
                    }
                }
            }

            // ======================================================== MODEL
            TarConsoleSection {
                width: body.width
                theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                title: con.tab === "cloud" ? "CLOUD MODELS" : "MODEL"
                note: con.tab === "cloud" ? "tap to switch the brain"
                    : (con.autotier ? "auto-picked for this machine"
                                    : "pinned — tap AUTO to release")

                Column {
                    width: parent.width
                    spacing: 1

                    Repeater {
                        model: con.modelsModel

                        Rectangle {
                            id: row
                            required property string name
                            required property string tier
                            required property string lane
                            required property real sizeGb
                            required property bool installed
                            required property bool active
                            required property string desc

                            readonly property string mName: name
                            readonly property string mDesc: desc
                            readonly property bool  mCloud: tier === "cloud"
                            readonly property string mTier: tier
                            readonly property string mLane: lane
                            readonly property real  mSize: sizeGb
                            readonly property bool  mHave: installed
                            readonly property bool  mActive: active

                            width: parent.width
                            visible: row.mCloud === (con.tab === "cloud")
                            height: visible ? con.s(row.mCloud ? 44 : 32) : 0
                            color: row.mActive
                                ? Qt.rgba(con.accent.r, con.accent.g, con.accent.b, 0.16)
                                : (hov.hovered
                                   ? Qt.rgba(con.theme.surface0.r, con.theme.surface0.g,
                                             con.theme.surface0.b, 0.55)
                                   : "transparent")
                            Behavior on color { ColorAnimation { duration: 120 } }

                            Rectangle {
                                width: con.s(2); height: parent.height
                                color: con.accent
                                visible: row.mActive
                            }

                            Row {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: con.s(10)
                                anchors.rightMargin: con.s(8)
                                spacing: con.s(7)

                                Text {
                                    width: con.s(10)
                                    text: row.mActive ? ">" : (row.mHave ? "·" : "")
                                    color: row.mActive ? con.accent : con.theme.green
                                    font.family: "JetBrains Mono"
                                    font.pixelSize: con.s(11)
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Column {
                                    width: parent.width - con.s(78)
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 0
                                    Text {
                                        width: parent.width
                                        text: row.mName
                                        color: row.mActive ? con.theme.text
                                                           : con.theme.subtext0
                                        font.family: "JetBrains Mono"
                                        font.pixelSize: con.s(10)
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: row.mCloud
                                            ? row.mLane + (row.mLane === "google" ? "   free tier" : "   paid")
                                              + (row.mHave ? "" : "   no key")
                                            : row.mTier + "/" + row.mLane
                                              + (row.mSize ? "   " + row.mSize + "GB" : "")
                                              + (row.mHave ? "" : "   not downloaded")
                                        color: row.mCloud
                                            ? (row.mHave ? con.theme.green : con.theme.overlay0)
                                            : (row.mLane === "open" ? con.theme.peach
                                                                    : con.theme.overlay0)
                                        font.family: "JetBrains Mono"
                                        font.pixelSize: con.s(8)
                                    }
                                    Text {
                                        width: parent.width
                                        visible: row.mCloud && row.mDesc !== ""
                                        text: row.mDesc
                                        color: con.theme.overlay0
                                        font.family: "JetBrains Mono"
                                        font.pixelSize: con.s(8)
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            HoverHandler { id: hov }
                            TapHandler { onTapped: con.pinModel(row.mName) }
                        }
                    }

                    Text {
                        visible: !con.modelsModel || con.modelsModel.count === 0
                        text: "scanning…"
                        color: con.theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: con.s(10)
                    }
                }
            }

            // ======================================================== VOICE
            TarConsoleSection {
                width: body.width
                theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                title: "VOICE"
                note: "no engine installed yet"

                Column {
                    width: parent.width
                    spacing: con.s(9)

                    Row {
                        spacing: con.s(7)
                        TarConsoleChip {
                            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                            text: con.micEnabled ? "MIC ON" : "MIC OFF"
                            active: con.micEnabled
                            onTapped: con.micToggled(!con.micEnabled)
                        }
                        TarConsoleChip {
                            theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                            text: con.speakReplies ? "READS ALOUD" : "SILENT"
                            active: con.speakReplies
                            onTapped: con.speakToggled(!con.speakReplies)
                        }
                    }

                    Item {
                        width: parent.width
                        height: con.s(26)

                        Text {
                            id: volLbl
                            text: "VOL"
                            color: con.theme.overlay1
                            font.family: "JetBrains Mono"
                            font.pixelSize: con.s(9)
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Item {
                            anchors.left: volLbl.right
                            anchors.leftMargin: con.s(10)
                            anchors.right: volPct.left
                            anchors.rightMargin: con.s(10)
                            anchors.verticalCenter: parent.verticalCenter
                            height: con.s(14)

                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width; height: con.s(3)
                                color: Qt.rgba(con.theme.surface1.r, con.theme.surface1.g,
                                               con.theme.surface1.b, 0.8)
                            }
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width * con.volume; height: con.s(3)
                                color: con.accent
                            }
                            Rectangle {
                                width: con.s(3); height: con.s(13)
                                color: con.theme.text
                                x: parent.width * con.volume - width / 2
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            MouseArea {
                                anchors.fill: parent
                                anchors.margins: -con.s(6)
                                onPressed: (m) => con.volumeRequested(
                                    Math.max(0, Math.min(1, m.x / width)))
                                onPositionChanged: (m) => {
                                    if (pressed) con.volumeRequested(
                                        Math.max(0, Math.min(1, m.x / width)));
                                }
                            }
                        }
                        Text {
                            id: volPct
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: Math.round(con.volume * 100) + "%"
                            color: con.theme.subtext0
                            font.family: "JetBrains Mono"
                            font.pixelSize: con.s(9)
                        }
                    }
                }
            }

            // ======================================================= MEMORY
            TarConsoleSection {
                width: body.width
                theme: con.theme; accent: con.accent; scaleFn: con.scaleFn
                title: "MEMORY"
                note: "say \"remember that …\" to add"

                Column {
                    width: parent.width
                    spacing: con.s(2)

                    Repeater {
                        model: con.memoryModel
                        Item {
                            id: memRow
                            required property string text
                            readonly property string factText_: text
                            width: parent.width
                            height: Math.max(con.s(24), factText.implicitHeight + con.s(8))

                            Text {
                                id: factText
                                anchors.left: parent.left
                                anchors.right: dropBtn.left
                                anchors.rightMargin: con.s(8)
                                anchors.verticalCenter: parent.verticalCenter
                                text: "· " + memRow.factText_
                                color: con.theme.subtext0
                                font.family: "JetBrains Mono"
                                font.pixelSize: con.s(10)
                                wrapMode: Text.Wrap
                            }
                            Text {
                                id: dropBtn
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                text: "✕"
                                color: dropHov.hovered ? con.theme.red : con.theme.overlay0
                                font.family: "JetBrains Mono"
                                font.pixelSize: con.s(11)
                                HoverHandler { id: dropHov }
                                TapHandler { onTapped: con.forgetFact(memRow.factText_) }
                            }
                        }
                    }

                    Text {
                        visible: !con.memoryModel || con.memoryModel.count === 0
                        text: "nothing remembered yet"
                        color: con.theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: con.s(10)
                    }
                }
            }
        }
    }

    // pinned action bar -- always reachable, never scrolls away
    Rectangle {
        id: actionBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: con.s(42)
        color: Qt.rgba(con.theme.mantle.r, con.theme.mantle.g, con.theme.mantle.b, 0.9)

        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: Qt.rgba(con.theme.surface1.r, con.theme.surface1.g,
                           con.theme.surface1.b, 0.6)
        }

        Row {
            anchors.centerIn: parent
            spacing: con.s(8)

            TarConsoleChip {
                theme: con.theme; accent: con.theme.peach; scaleFn: con.scaleFn
                text: "↶ UNDO LAST"
                onTapped: con.undoRequested()
            }
            TarConsoleChip {
                theme: con.theme; accent: con.theme.red; scaleFn: con.scaleFn
                text: "✕ CLOSE"
                onTapped: con.closed()
            }
        }
    }

    Keys.onEscapePressed: (e) => { con.closed(); e.accepted = true; }
}
