import QtQuick
import Quickshell
import Quickshell.Io
import "."

// T.A.R. background-tasks tab -- the mirror image of the CONSOLE tab.
// While a task runs, a TASKS button sits on the right edge (pulsing dot +
// round badge). Clicking it slides a drawer in from the right: the task,
// running time, time left (with a bar), the round, a live REASONING feed of
// what it sees / decides / does each round, and PAUSE/RESUME + KILL.
// Reads loop.json (written by the task itself) once a second.
Item {
    id: tasks
    anchors.fill: parent

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property var task: null             // parsed loop.json, or null
    property bool open: false
    property real now: Date.now() / 1000

    readonly property bool active: task !== null
    onActiveChanged: if (active) open = true     // a new task: show it once
    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    readonly property string tools: Quickshell.env("HOME")
        + "/.config/hypr/scripts/quickshell/tar/tar_tools.py"

    // ---------------------------------------------------------------- data
    Process {
        id: reader
        command: ["cat", tasks.dataDir + "/loop.json"]
        stdout: StdioCollector {
            onStreamFinished: {
                var t = (this.text || "").trim();
                if (t === "") { tasks.task = null; return; }
                try { tasks.task = JSON.parse(t); } catch (e) { /* mid-write: keep last */ }
            }
        }
        onExited: (code) => { if (code !== 0) tasks.task = null; }
    }
    Timer {
        interval: 1000; repeat: true; running: true; triggeredOnStart: true
        onTriggered: { tasks.now = Date.now() / 1000; if (!reader.running) reader.running = true; }
    }
    Process { id: ctl; property var argv: []; command: argv }
    function control(action) {
        ctl.argv = ["python3", tasks.tools, "run", action];
        ctl.running = true;
        reader.running = true;
    }

    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec));
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), ss = sec % 60;
        return (h > 0 ? h + "h " : "") + (h > 0 || m > 0 ? m + "m " : "") + ss + "s";
    }
    readonly property bool paused: active && !!task.paused
    readonly property string taskStatus: !active ? "" :
        (paused ? "paused"
         : (now - (task.beat || now) > 120 ? "not responding" : (task.status || "running")))
    readonly property real elapsed: active
        ? (paused ? (task.paused_at || now) : now) - (task.started || now) : 0
    readonly property real timeLeft: active && task.until
        ? (task.until - (paused ? (task.paused_at || now) : now)) : -1
    readonly property real total: active && task.until ? (task.until - task.started) : 0
    readonly property color dotColor: paused ? theme.yellow
        : (taskStatus === "not responding" || taskStatus === "failed") ? theme.red : theme.green

    // ---------------------------------------------------------------- the tab
    Item {
        id: tab
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.rightMargin: tasks.s(12)
        width: btn.width
        height: btn.height
        visible: tasks.active
        opacity: tasks.open ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: 180 } }

        TarHudButton {
            id: btn
            theme: tasks.theme; accent: tasks.accent; scaleFn: tasks.scaleFn
            glyph: "\u{f0954}"
            label: "TASKS"
            active: tasks.active
            onClicked: tasks.open = true
        }
        Rectangle {                     // live dot
            width: tasks.s(7); height: width; radius: width / 2
            anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: -tasks.s(2)
            color: tasks.dotColor
            SequentialAnimation on opacity {
                loops: Animation.Infinite
                running: tab.visible && !tasks.paused
                NumberAnimation { to: 0.25; duration: 650 }
                NumberAnimation { to: 1.0; duration: 650 }
            }
        }
        Text {                          // round badge
            anchors.top: parent.bottom
            anchors.topMargin: tasks.s(3)
            anchors.horizontalCenter: parent.horizontalCenter
            text: tasks.active ? "R" + (tasks.task.round || 0) : ""
            color: tasks.theme.overlay1
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(8)
        }
    }

    // ---------------------------------------------------------------- drawer
    Item {
        id: drawer
        width: tasks.s(330)
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.topMargin: tasks.s(6)
        anchors.bottomMargin: tasks.s(6)
        visible: tasks.active && opacity > 0.01
        opacity: tasks.open ? 1 : 0
        transform: Translate {
            x: tasks.open ? 0 : drawer.width + tasks.s(14)
            Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutExpo } }
        }
        Behavior on opacity { NumberAnimation { duration: 200 } }

        // swallow clicks so they don't fall through to the chat behind
        MouseArea { anchors.fill: parent; hoverEnabled: true }

        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(tasks.theme.crust.r, tasks.theme.crust.g, tasks.theme.crust.b, 0.95)
        }
        Rectangle {                     // seam on the inner edge, like the console
            anchors.left: parent.left
            width: 1; height: parent.height
            color: Qt.rgba(tasks.accent.r, tasks.accent.g, tasks.accent.b, 0.45)
        }

        Column {
            id: head
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: tasks.s(16)
            spacing: tasks.s(8)

            Item {
                width: head.width
                height: tasks.s(18)
                Text {
                    text: "◢ TAR//TASKS"
                    color: tasks.accent
                    font.family: "JetBrains Mono"
                    font.pixelSize: tasks.s(13)
                    font.bold: true
                    font.letterSpacing: tasks.s(1.5)
                }
                Text {                  // close arrow
                    anchors.right: parent.right
                    text: "▶"
                    color: closeHov.hovered ? tasks.theme.text : tasks.theme.overlay1
                    font.pixelSize: tasks.s(12)
                    HoverHandler { id: closeHov }
                    TapHandler { onTapped: tasks.open = false }
                }
            }

            Row {
                spacing: tasks.s(6)
                Rectangle {
                    width: tasks.s(7); height: width; radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: tasks.dotColor
                }
                Text {
                    text: tasks.taskStatus.toUpperCase()
                          + (tasks.active ? "   ·   ROUND " + (tasks.task.round || 0) : "")
                    color: tasks.theme.subtext1
                    font.family: "JetBrains Mono"
                    font.pixelSize: tasks.s(9)
                    font.letterSpacing: tasks.s(1)
                }
            }

            Text {
                width: head.width
                text: tasks.active ? tasks.task.task : ""
                color: tasks.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: tasks.s(10)
                wrapMode: Text.Wrap
                maximumLineCount: 4
                elide: Text.ElideRight
            }

            // running / left + progress bar
            Row {
                width: head.width
                Text {
                    width: head.width / 2
                    text: "running  " + tasks.fmt(tasks.elapsed)
                    color: tasks.theme.subtext0
                    font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
                }
                Text {
                    width: head.width / 2
                    horizontalAlignment: Text.AlignRight
                    text: tasks.timeLeft >= 0 ? "left  " + tasks.fmt(tasks.timeLeft) : "no time limit"
                    color: tasks.theme.subtext0
                    font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
                }
            }
            Rectangle {
                width: head.width; height: tasks.s(3)
                color: Qt.rgba(tasks.theme.surface1.r, tasks.theme.surface1.g, tasks.theme.surface1.b, 0.6)
                visible: tasks.total > 0
                Rectangle {
                    height: parent.height
                    width: tasks.total > 0
                        ? parent.width * Math.min(1, Math.max(0, tasks.elapsed / tasks.total)) : 0
                    color: tasks.paused ? tasks.theme.yellow : tasks.accent
                }
            }
            Text {
                visible: tasks.active && tasks.task.status === "waiting" && !tasks.paused
                text: tasks.active ? "next round in " + tasks.fmt((tasks.task.next_at || tasks.now) - tasks.now) : ""
                color: tasks.theme.overlay1
                font.family: "JetBrains Mono"; font.pixelSize: tasks.s(8)
            }

            Row {
                spacing: tasks.s(7)
                TarConsoleChip {
                    theme: tasks.theme; accent: tasks.accent; scaleFn: tasks.scaleFn
                    text: tasks.paused ? "▶ RESUME" : "❚❚ PAUSE"
                    active: tasks.paused
                    onTapped: tasks.control(tasks.paused ? "resume_task" : "pause_task")
                }
                TarConsoleChip {
                    theme: tasks.theme; accent: tasks.theme.red; scaleFn: tasks.scaleFn
                    text: "✕ KILL"
                    onTapped: tasks.control("stop_task")
                }
            }

            Rectangle {
                width: head.width; height: 1
                color: Qt.rgba(tasks.theme.surface1.r, tasks.theme.surface1.g, tasks.theme.surface1.b, 0.6)
            }
            Text {
                text: "REASONING"
                color: tasks.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: tasks.s(10)
                font.bold: true
                font.letterSpacing: tasks.s(2)
            }
        }

        // live reasoning feed: newest at the bottom, auto-scrolled
        ListView {
            id: feed
            anchors.top: head.bottom
            anchors.topMargin: tasks.s(8)
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: tasks.s(16)
            anchors.rightMargin: tasks.s(16)
            anchors.bottomMargin: tasks.s(14)
            clip: true
            spacing: tasks.s(6)
            model: tasks.active ? (tasks.task.feed || []) : []
            onCountChanged: Qt.callLater(feed.positionViewAtEnd)
            delegate: Row {
                required property var modelData
                width: feed.width
                spacing: tasks.s(7)
                Rectangle {
                    width: tasks.s(2)
                    height: line.implicitHeight
                    color: modelData.k === "think" ? tasks.accent
                         : modelData.k === "warn" ? tasks.theme.red
                         : modelData.k === "round" ? tasks.theme.surface2
                         : tasks.theme.green
                }
                Text {
                    id: line
                    width: feed.width - tasks.s(9)
                    text: (modelData.k === "think" ? "“" + modelData.t + "”" : modelData.t)
                    color: modelData.k === "think" ? tasks.theme.text
                         : modelData.k === "round" ? tasks.theme.overlay0
                         : tasks.theme.subtext0
                    font.family: "JetBrains Mono"
                    font.pixelSize: tasks.s(modelData.k === "round" ? 8 : 9)
                    font.italic: modelData.k === "think"
                    wrapMode: Text.Wrap
                }
            }
            Text {
                visible: feed.count === 0
                text: "waiting for the first round…"
                color: tasks.theme.overlay0
                font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
            }
        }
    }
}
