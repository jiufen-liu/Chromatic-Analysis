import QtQuick 2.15
import QtQuick3D 6.5

Item {
    id: root
    property string displayMode: "points"
    property real pointScale: 0.032
    property real pointOpacity: 1.0
    property real surfaceOpacity: 0.22
    property bool showAxes: true
    property bool showGrid: true
    property bool showLabels: true
    property bool autoRotate: false
    property int resetToken: 0
    property int presetIndex: -1
    property real yaw: -38
    property real pitch: 18
    property real cameraZ: 410
    // Right-button pan translates the whole analytical scene, not the camera.
    // This keeps data, grid, axes and projected axis labels physically attached.
    property real scenePanX: 0
    property real scenePanY: 0
    property point pressPos: Qt.point(0,0)
    property real pressYaw: 0
    property real pressPitch: 0
    property real pressScenePanX: 0
    property real pressScenePanY: 0

    onResetTokenChanged: resetView()
    onPresetIndexChanged: setPreset(presetIndex)

    function resetView() { yaw=-38; pitch=18; cameraZ=410; scenePanX=0; scenePanY=0; }
    function setPreset(i) {
        if (i===0) { yaw=-38; pitch=18; }
        else if (i===1) { yaw=0; pitch=0; }
        else if (i===2) { yaw=-90; pitch=0; }
        else if (i===3) { yaw=0; pitch=78; }
        else if (i===4) { yaw=0; pitch=-78; }
        cameraZ=410
        scenePanX=0
        scenePanY=0
    }
    function zoomBy(delta) { cameraZ=Math.max(185,Math.min(760,cameraZ+delta)); }
    function panUnitsPerPixel() {
        // Perspective-correct pan speed: one mouse pixel maps to the same
        // apparent screen distance at every zoom level.
        var visibleWorldHeight = 2.0 * cameraZ * Math.tan(cam.fieldOfView * Math.PI / 360.0)
        return visibleWorldHeight / Math.max(1, height)
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        border.color: "#CDD4DB"
        border.width: 1
        gradient: Gradient {
            // HF45 Pearl profile: clean Apple-like studio canvas.  It is not
            // pure white, so pale/yellow/white samples still retain separation.
            GradientStop { position: 0.0; color: "#FAFBFC" }
            GradientStop { position: 0.54; color: "#F2F4F6" }
            GradientStop { position: 1.0; color: "#E8EBEF" }
        }
    }

    View3D {
        id: view
        anchors.fill: parent
        anchors.margins: 1
        camera: cam
        environment: SceneEnvironment {
            backgroundMode: SceneEnvironment.Transparent
            clearColor: "#00000000"
            // HF46: dense 3,000+ point libraries prioritize interaction FPS.
            // The marker mesh is already smooth at its tiny screen size, so
            // disabling MSAA on dense clouds saves a sizeable integrated-GPU
            // full-frame cost without removing any measured samples.
            antialiasingMode: bridge.sampleCount >= 3000 ? SceneEnvironment.NoAA : SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.Low
            temporalAAEnabled: false
            // Ambient occlusion made dense 2k-3.5k clouds darker and adds a
            // costly full-screen pass. The reference uses soft fill instead.
            aoEnabled: false
        }

        PerspectiveCamera {
            id: cam
            x: 0
            y: 0
            z: root.cameraZ
            clipNear: 1
            clipFar: 1600
            fieldOfView: 27
        }

        // HF45 Pearl studio profile: four broad fills approximate the reference
        // render's ambient/soft-box look.  No side of a marker falls into a hard
        // black shadow, while a restrained white highlight keeps it spherical.
        DirectionalLight {
            eulerRotation.x: -28
            eulerRotation.y: -32
            brightness: 1.18
            color: "#FFFDFC"
        }
        DirectionalLight {
            eulerRotation.x: 18
            eulerRotation.y: 148
            brightness: 0.82
            color: "#F5F8FF"
        }
        DirectionalLight {
            eulerRotation.x: 68
            eulerRotation.y: 20
            brightness: 0.52
            color: "#FFF8F2"
        }
        DirectionalLight {
            eulerRotation.x: -10
            eulerRotation.y: 78
            brightness: 0.34
            color: "#FFFFFF"
        }

        Node {
            id: sceneRoot
            // Translate the entire colour-space scene for right-button pan.
            // Position is in the camera-facing parent plane, so screen pan feels
            // natural while rotations remain local to the same scene.
            position: Qt.vector3d(root.scenePanX, root.scenePanY, 0)
            eulerRotation.x: root.pitch
            eulerRotation.y: root.yaw

            // Axis endpoints live inside the same rotating scene as the data.
            // Screen-space text below projects these exact 3D locations, so the
            // labels follow the coordinate system while staying readable.
            Node { id: axisLPlus;  position: Qt.vector3d(0, 61, 0) }
            Node { id: axisLMinus; position: Qt.vector3d(0, -61, 0) }
            Node { id: axisAPlus;  position: Qt.vector3d(122, 0, 0) }
            Node { id: axisAMinus; position: Qt.vector3d(-122, 0, 0) }
            Node { id: axisBPlus;  position: Qt.vector3d(0, 0, 122) }
            Node { id: axisBMinus; position: Qt.vector3d(0, 0, -122) }

            FileInstancing {
                id: sampleTable
                source: bridge.instanceUrl
                depthSortingEnabled: false
                hasTransparency: root.pointOpacity < 0.999
            }

            Model {
                id: pointModel
                visible: root.displayMode === "points" || root.displayMode === "both"
                geometry: sphereGeometry
                instancing: sampleTable
                scale: Qt.vector3d(root.pointScale, root.pointScale, root.pointScale)
                pickable: true
                castsShadows: false
                receivesShadows: false
                // ChromaShare-like diffuse marker material.  DefaultMaterial's
                // soft fragment lighting avoids the dark PBR backsides that made
                // blue/purple/red points look muddy in HF43.
                materials: DefaultMaterial {
                    lighting: DefaultMaterial.FragmentLighting
                    diffuseColor: "#FFFFFF"
                    vertexColorsEnabled: true
                    opacity: root.pointOpacity
                    // Soft pearl: a wide low-energy highlight, not glossy plastic.
                    specularAmount: 0.24
                    specularRoughness: 0.56
                }
            }

            Model {
                id: surfaceModel
                visible: root.displayMode === "surface" || root.displayMode === "both"
                geometry: gamutGeometry
                castsShadows: false
                receivesShadows: false
                materials: DefaultMaterial {
                    // HF46: the envelope is an analytical overlay, not a solid
                    // object. Unlit vertex colour makes it reliable on every
                    // RHI backend and avoids missing-normal/PBR artefacts.
                    lighting: DefaultMaterial.NoLighting
                    diffuseColor: "#FFFFFF"
                    vertexColorsEnabled: true
                    opacity: root.surfaceOpacity
                    cullMode: Material.NoCulling
                    blendMode: DefaultMaterial.SourceOver
                }
            }

            Model {
                visible: root.showGrid
                geometry: gridGeometry
                castsShadows: false
                receivesShadows: false
                materials: DefaultMaterial {
                    lighting: DefaultMaterial.NoLighting
                    diffuseColor: "#FFFFFF"
                    opacity: 0.025
                    lineWidth: 1
                }
            }

            Model {
                visible: root.showAxes
                geometry: axisGeometry
                castsShadows: false
                receivesShadows: false
                materials: DefaultMaterial {
                    lighting: DefaultMaterial.NoLighting
                    diffuseColor: "#5B6572"
                    opacity: 0.62
                    lineWidth: 1.35
                }
            }

            Node {
                id: selectedAnchor
                visible: bridge.hasSelection
                position: Qt.vector3d(bridge.selectedSceneX, bridge.selectedSceneY, bridge.selectedSceneZ)

                Model {
                    visible: bridge.hasSelection
                    source: "#Sphere"
                    scale: Qt.vector3d(root.pointScale*1.72, root.pointScale*1.72, root.pointScale*1.72)
                    castsShadows: false
                    receivesShadows: false
                    materials: DefaultMaterial {
                        lighting: DefaultMaterial.FragmentLighting
                        diffuseColor: bridge.selectedHex
                        specularAmount: 0.24
                        specularRoughness: 0.56
                        opacity: 1.0
                    }
                }
                Model {
                    visible: bridge.hasSelection
                    source: "#Sphere"
                    scale: Qt.vector3d(root.pointScale*2.45, root.pointScale*2.45, root.pointScale*2.45)
                    castsShadows: false
                    receivesShadows: false
                    materials: PrincipledMaterial {
                        baseColor: "#2E83FF"
                        emissiveFactor: Qt.vector3d(0.10,0.22,0.55)
                        roughness: 0.4
                        opacity: 0.22
                    }
                }
            }
        }
    }

    // Minimal analytical toolbar. All controls call the same camera used by the
    // right-side buttons so there is no second interaction implementation.
    Row {
        id: toolbar
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 12
        spacing: 5
        z: 100
        Repeater {
            model: ["⟳","＋","－","⌂"]
            delegate: Rectangle {
                width: 34; height: 30; radius: 8
                color: "#F2FFFFFF"; border.color: "#66D6DCE4"
                Text { anchors.centerIn: parent; text: modelData; color: "#405066"; font.pixelSize: 16; font.bold: true }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (index===0) root.yaw += 15
                        else if (index===1) root.zoomBy(-36)
                        else if (index===2) root.zoomBy(36)
                        else root.resetView()
                    }
                }
            }
        }
    }

    // Axis labels are projected from the actual rotating 3D endpoints.
    // This keeps the coordinate system visually attached to the data instead
    // of pinning labels to the screen edges.
    property vector3d axisLpScreen: view.mapFrom3DScene(axisLPlus.scenePosition)
    property vector3d axisLmScreen: view.mapFrom3DScene(axisLMinus.scenePosition)
    property vector3d axisApScreen: view.mapFrom3DScene(axisAPlus.scenePosition)
    property vector3d axisAmScreen: view.mapFrom3DScene(axisAMinus.scenePosition)
    property vector3d axisBpScreen: view.mapFrom3DScene(axisBPlus.scenePosition)
    property vector3d axisBmScreen: view.mapFrom3DScene(axisBMinus.scenePosition)

    component AxisLabel: Text {
        required property vector3d screenPoint
        required property string axisText
        visible: root.showAxes && root.showLabels && screenPoint.z > -1
                 && screenPoint.x > -80 && screenPoint.x < root.width + 80
                 && screenPoint.y > -60 && screenPoint.y < root.height + 60
        text: axisText
        // Do not clamp to screen edges: labels are part of the analytical scene
        // and must slide out of view together with their real 3D axis endpoints.
        x: screenPoint.x-width/2
        y: screenPoint.y-height/2
        color: "#44505E"
        font.pixelSize: 15
        font.bold: true
        z: 120
    }
    AxisLabel { screenPoint: root.axisLpScreen; axisText: "+L*" }
    AxisLabel { screenPoint: root.axisLmScreen; axisText: "-L*" }
    AxisLabel { screenPoint: root.axisApScreen; axisText: "+a*" }
    AxisLabel { screenPoint: root.axisAmScreen; axisText: "-a*" }
    AxisLabel { screenPoint: root.axisBpScreen; axisText: "+b*" }
    AxisLabel { screenPoint: root.axisBmScreen; axisText: "-b*" }

    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        hoverEnabled: true
        cursorShape: (pressedButtons & Qt.RightButton) ? Qt.ClosedHandCursor : Qt.ArrowCursor
        onPressed: function(m) {
            root.pressPos=Qt.point(m.x,m.y)
            root.pressYaw=root.yaw
            root.pressPitch=root.pitch
            root.pressScenePanX=root.scenePanX
            root.pressScenePanY=root.scenePanY
        }
        onPositionChanged: function(m) {
            var dx = m.x-root.pressPos.x
            var dy = m.y-root.pressPos.y
            if (pressedButtons & Qt.RightButton) {
                // Scheme A: right-button drag pans/slides the whole 3D view in
                // screen space.  The scale follows zoom for a consistent feel.
                var u = root.panUnitsPerPixel()
                // Translate the scene itself so points, grid, axes and all
                // projected coordinate labels stay locked together.
                root.scenePanX = root.pressScenePanX + dx*u
                root.scenePanY = root.pressScenePanY - dy*u
            } else if (pressedButtons & Qt.LeftButton) {
                // Left-button drag is orbit/rotation only.
                root.yaw = root.pressYaw + dx*0.30
                root.pitch = Math.max(-88,Math.min(88,root.pressPitch+dy*0.24))
            }
        }
        onWheel: function(w) {
            root.cameraZ=Math.max(185,Math.min(760,root.cameraZ-w.angleDelta.y*0.19))
            w.accepted=true
        }
        onClicked: function(m) {
            // Only a left click changes selection.  Right click is reserved for
            // pan, so it never opens/clears a sample while positioning the view.
            if (m.button !== Qt.LeftButton)
                return
            var hit=view.pick(m.x,m.y)
            if (hit && hit.objectHit === pointModel && hit.instanceIndex >= 0)
                bridge.selectInstance(hit.instanceIndex)
            else
                bridge.clearSelection()
        }
        onDoubleClicked: function(m) {
            if (m.button === Qt.LeftButton) root.resetView()
        }
    }

    // HF46: selection is intentionally shown only as the 3D highlight rings.
    // Numeric details already live in the right information panel, so the old
    // floating callout was redundant and obscured the colour-space view.

    Timer { interval: 16; repeat: true; running: root.autoRotate; onTriggered: root.yaw += 0.14 }

    Rectangle {
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 14
        width: 246; height: 36; radius: 9; color: "#DAFFFFFF"; border.color: "#55FFFFFF"; z: 100
        Text { anchors.centerIn: parent; text: "左键旋转 · 右键平移 · 滚轮缩放 · 双击复位"; color: "#475568"; font.pixelSize: 12 }
    }
}
