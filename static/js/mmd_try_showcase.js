window.onerror = function(msg, url, line, col, error) {
    if (String(msg || '').includes("ResizeObserver")) return;
    console.error("Global Error:", msg, "Line:", line, error);
};

const MODEL_URL = '/MMD/TDA%20Default%20Miku%20%28Xoriu%20Ver.%29/TDA%20Default%20Miku%20%28Xoriu%20Ver%29.pmx';

let motionFiles = [];
let motionEntries = [];
let currentMotionIndex = 0;
let isLooping = true;
let currentMesh = null;
let modelLoadGeneration = 0;
let animationHelper = null;
let scene, camera, renderer, controls, loader;
let mixer = null;
let initialPosition = null;
let initialFootPosition = null;

let lastPhysicsResetTime = Date.now();
let physicsEnableTimer = null;
const PHYSICS_RESET_INTERVAL = 30000;
let lastPhysicsPanicTime = 0;
let abnormalPhysicsFrames = 0;
const PHYSICS_PANIC_COOLDOWN = 1200;
const PHYSICS_MAX_ROOT_DRIFT = 8.0;
const PHYSICS_MAX_FOOT_DRIFT = 14.0;
const PHYSICS_MAX_FRAME_Y_DELTA = 3.5;

const vmdCache = new Map();

const FIXED_STEP = 1 / 60;
let motionSpeed = 1.0;
const clock = new THREE.Clock();
let accumulator = 0;

let mainAmbientLight, mainDirLight, mainTopLight;
let initialCameraDistance = 0;
const BASE_AMBIENT = 0.7;
const BASE_DIR = 0.8;
const BASE_TOP = 0.5;

let activeAction = null;
let activeFinishedHandler = null;
let activeMotionToken = 0;
let motionLoadToken = 0;
let motionFinishTimer = null;
let motionLoopTimer = null;
let motionFallbackTimer = null;
let motionStatus = 'Idle';
let motionStatusChangedAt = Date.now();
const MOTION_MARKS_STORAGE_KEY = 'mmd_try_showcase_motion_marks_v1';
const MOTION_ANNOTATIONS_STORAGE_KEY = 'mmd_try_showcase_motion_annotations_v1';
let motionMarks = loadMotionMarks();
let motionAnnotations = loadMotionAnnotations();
let isExportingMotions = false;
let lastExportResult = null;

document.addEventListener('DOMContentLoaded', () => {
    initDraggableModel();
    initMotionListControls();
    const nextButton = document.getElementById('next-motion');
    if (nextButton) {
        nextButton.addEventListener('click', playNextMotion);
    }
    const exportButton = document.getElementById('export-checked-motions');
    if (exportButton) {
        exportButton.addEventListener('click', (event) => {
            event.stopPropagation();
            exportCheckedMotions();
        });
    }
});

function setLoading(title, detail, hidden = false) {
    const panel = document.getElementById('loading-panel');
    const titleNode = document.getElementById('loading-title');
    const detailNode = document.getElementById('loading-detail');
    if (titleNode) titleNode.textContent = title || '';
    if (detailNode) detailNode.textContent = detail || '';
    if (panel) panel.classList.toggle('hidden', Boolean(hidden));
}

function updateMotionHud(status) {
    const nameNode = document.getElementById('motion-name');
    const detailNode = document.getElementById('motion-detail');
    const nextButton = document.getElementById('next-motion');
    const listButton = document.getElementById('motion-list-toggle');
    const entry = motionEntries[currentMotionIndex] || {};
    const motionName = getMotionDisplayName(entry, currentMotionIndex) || 'No motion';
    if (status) {
        motionStatus = status;
        motionStatusChangedAt = Date.now();
    }
    if (nameNode) nameNode.textContent = motionName;
    if (detailNode) {
        const countText = motionFiles.length ? `${currentMotionIndex + 1} / ${motionFiles.length}` : '0 / 0';
        detailNode.textContent = status ? `${status} - ${countText}` : countText;
    }
    if (nextButton) nextButton.disabled = !currentMesh || motionFiles.length < 2;
    if (listButton) listButton.disabled = motionFiles.length === 0;
    updateMotionExportState();
    updateMotionListActiveState();
    updateMotionDebugState();
}

function getMotionNameFromUrl(url) {
    if (!url) return '';
    try {
        const path = decodeURIComponent(String(url).split('?')[0]);
        const fileName = path.split('/').filter(Boolean).pop() || '';
        return fileName.replace(/\.vmd$/i, '');
    } catch (e) {
        return String(url).split('/').pop() || '';
    }
}

function loadMotionMarks() {
    try {
        const raw = window.localStorage ? window.localStorage.getItem(MOTION_MARKS_STORAGE_KEY) : null;
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
        console.warn("Motion marks load failed", e);
        return {};
    }
}

function saveMotionMarks() {
    try {
        if (window.localStorage) {
            window.localStorage.setItem(MOTION_MARKS_STORAGE_KEY, JSON.stringify(motionMarks));
        }
    } catch (e) {
        console.warn("Motion marks save failed", e);
    }
}

function loadMotionAnnotations() {
    try {
        const raw = window.localStorage ? window.localStorage.getItem(MOTION_ANNOTATIONS_STORAGE_KEY) : null;
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
        console.warn("Motion annotations load failed", e);
        return {};
    }
}

function saveMotionAnnotations() {
    try {
        if (window.localStorage) {
            window.localStorage.setItem(MOTION_ANNOTATIONS_STORAGE_KEY, JSON.stringify(motionAnnotations));
        }
    } catch (e) {
        console.warn("Motion annotations save failed", e);
    }
}

function getMotionKey(entry, index) {
    return entry?.relative_path || entry?.url || motionFiles[index] || String(index);
}

function getMotionDisplayName(entry = {}, index = 0, options = {}) {
    const keepExtension = Boolean(options.keepExtension);
    const raw = entry.relative_path || entry.name || getMotionNameFromUrl(entry.url || motionFiles[index]) || `Motion ${index + 1}`;
    return keepExtension ? raw : raw.replace(/\.vmd$/i, '');
}

function getMotionAnnotation(key) {
    return String(motionAnnotations[key] || '').trim();
}

function setMotionAnnotation(index, annotation) {
    const entry = motionEntries[index] || {};
    const key = getMotionKey(entry, index);
    if (!key) return;

    const trimmed = String(annotation || '').trim();
    if (trimmed) {
        motionAnnotations[key] = trimmed;
    } else {
        delete motionAnnotations[key];
    }
    saveMotionAnnotations();
    updateMotionDebugState();
}

function getMotionRelativePath(entry = {}, index = 0) {
    if (entry.relative_path) return entry.relative_path;

    const url = entry.url || motionFiles[index] || '';
    try {
        const decodedPath = decodeURIComponent(String(url).split('?')[0]);
        const marker = '/motion/try/';
        const markerIndex = decodedPath.toLowerCase().indexOf(marker);
        if (markerIndex >= 0) {
            return decodedPath.slice(markerIndex + marker.length);
        }
    } catch (e) {
        console.warn("Motion relative path parse failed", e);
    }
    return '';
}

function getCheckedMotionEntries() {
    return motionEntries
        .map((entry, index) => ({
            entry,
            index,
            key: getMotionKey(entry, index),
            relativePath: getMotionRelativePath(entry, index),
            annotation: getMotionAnnotation(getMotionKey(entry, index))
        }))
        .filter(item => item.relativePath && motionMarks[item.key] === 'check');
}

function updateMotionExportState() {
    const exportButton = document.getElementById('export-checked-motions');
    if (!exportButton) return;

    const checkedCount = getCheckedMotionEntries().length;
    exportButton.disabled = isExportingMotions || motionEntries.length === 0;
    exportButton.textContent = isExportingMotions ? 'Exporting...' : 'Export';
    exportButton.title = checkedCount
        ? `Export ${checkedCount} checked motion${checkedCount === 1 ? '' : 's'}`
        : 'Click after marking motions with check';
}

async function exportCheckedMotions() {
    if (isExportingMotions) return;

    const checkedEntries = getCheckedMotionEntries();
    if (!checkedEntries.length) {
        updateMotionHud('No checked motions');
        return;
    }

    isExportingMotions = true;
    updateMotionExportState();
    updateMotionHud(`Exporting ${checkedEntries.length}`);

    try {
        const response = await fetch('/export_checked_motions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                relative_paths: checkedEntries.map(item => item.relativePath),
                annotations: checkedEntries.reduce((annotations, item) => {
                    if (item.annotation) {
                        annotations[item.relativePath] = item.annotation;
                    }
                    return annotations;
                }, {})
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || `Export failed (${response.status})`);
        }

        lastExportResult = data;
        const copiedCount = Number(data.copied_count || 0);
        const skippedCount = Array.isArray(data.skipped) ? data.skipped.length : 0;
        const status = skippedCount
            ? `Exported ${copiedCount}, skipped ${skippedCount}`
            : `Exported ${copiedCount}`;
        updateMotionHud(status);
        if (data.export_dir) {
            alert(`Exported ${copiedCount} checked motion file${copiedCount === 1 ? '' : 's'} to:\n${data.export_dir}`);
        }
    } catch (error) {
        console.error('Export checked motions failed:', error);
        updateMotionHud('Export failed');
        alert(error.message || String(error));
    } finally {
        isExportingMotions = false;
        updateMotionExportState();
        updateMotionDebugState();
    }
}

function initMotionListControls() {
    const toggleButton = document.getElementById('motion-list-toggle');
    const closeButton = document.getElementById('motion-list-close');
    const panel = document.getElementById('motion-list-panel');

    if (toggleButton) {
        toggleButton.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMotionList();
        });
    }

    if (closeButton) {
        closeButton.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMotionList(false);
        });
    }

    if (panel) {
        panel.addEventListener('click', (event) => {
            event.stopPropagation();
        });
    }

    document.addEventListener('click', () => {
        toggleMotionList(false);
    });
}

function toggleMotionList(forceOpen) {
    const panel = document.getElementById('motion-list-panel');
    if (!panel) return;

    const shouldOpen = typeof forceOpen === 'boolean'
        ? forceOpen
        : panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !shouldOpen);

    if (shouldOpen) {
        renderMotionList();
        scrollActiveMotionIntoView();
    }
}

function renderMotionList() {
    const listNode = document.getElementById('motion-list');
    const titleNode = document.getElementById('motion-list-title');
    if (titleNode) {
        const checkedCount = getCheckedMotionEntries().length;
        titleNode.textContent = checkedCount
            ? `Motions (${motionFiles.length}, ${checkedCount} checked)`
            : `Motions (${motionFiles.length})`;
    }
    if (!listNode) return;

    listNode.innerHTML = '';
    if (!motionEntries.length) {
        const emptyNode = document.createElement('div');
        emptyNode.className = 'motion-empty';
        emptyNode.textContent = 'No VMD files';
        listNode.appendChild(emptyNode);
        updateMotionExportState();
        return;
    }

    motionEntries.forEach((entry, index) => {
        const row = document.createElement('div');
        row.className = 'motion-row';
        row.dataset.index = String(index);
        row.classList.toggle('active', index === currentMotionIndex);

        const indexNode = document.createElement('span');
        indexNode.className = 'motion-index';
        indexNode.textContent = String(index + 1).padStart(2, '0');

        const selectButton = document.createElement('button');
        selectButton.className = 'motion-select';
        selectButton.type = 'button';
        selectButton.textContent = getMotionDisplayName(entry, index, { keepExtension: true });
        selectButton.title = selectButton.textContent;
        selectButton.addEventListener('click', () => {
            switchToMotionIndex(index, 'selected motion');
        });

        const key = getMotionKey(entry, index);
        const mark = motionMarks[key] || '';
        const annotationInput = document.createElement('input');
        annotationInput.className = 'motion-annotation';
        annotationInput.type = 'text';
        annotationInput.placeholder = 'Annotation';
        annotationInput.value = motionAnnotations[key] || '';
        annotationInput.title = 'Export name annotation';
        annotationInput.addEventListener('input', () => {
            setMotionAnnotation(index, annotationInput.value);
        });
        annotationInput.addEventListener('click', (event) => {
            event.stopPropagation();
        });
        annotationInput.addEventListener('keydown', (event) => {
            event.stopPropagation();
        });

        const checkButton = document.createElement('button');
        checkButton.className = 'motion-mark check';
        checkButton.type = 'button';
        checkButton.title = 'Mark ok';
        checkButton.innerHTML = '&#10003;';
        checkButton.classList.toggle('active', mark === 'check');
        checkButton.addEventListener('click', () => {
            setMotionMark(index, 'check');
        });

        const crossButton = document.createElement('button');
        crossButton.className = 'motion-mark cross';
        crossButton.type = 'button';
        crossButton.title = 'Mark bad';
        crossButton.innerHTML = '&#10005;';
        crossButton.classList.toggle('active', mark === 'cross');
        crossButton.addEventListener('click', () => {
            setMotionMark(index, 'cross');
        });

        row.appendChild(indexNode);
        row.appendChild(selectButton);
        row.appendChild(annotationInput);
        row.appendChild(checkButton);
        row.appendChild(crossButton);
        listNode.appendChild(row);
    });
    updateMotionExportState();
}

function setMotionMark(index, mark) {
    const entry = motionEntries[index] || {};
    const key = getMotionKey(entry, index);
    if (!key) return;

    if (motionMarks[key] === mark) {
        delete motionMarks[key];
    } else {
        motionMarks[key] = mark;
    }
    saveMotionMarks();
    renderMotionList();
    scrollActiveMotionIntoView();
    updateMotionExportState();
    updateMotionDebugState();
}

function updateMotionListActiveState() {
    const rows = document.querySelectorAll('.motion-row');
    rows.forEach((row) => {
        row.classList.toggle('active', Number(row.dataset.index) === currentMotionIndex);
    });
}

function scrollActiveMotionIntoView() {
    const panel = document.getElementById('motion-list-panel');
    if (!panel || panel.classList.contains('hidden')) return;
    const activeRow = panel.querySelector(`.motion-row[data-index="${currentMotionIndex}"]`);
    if (activeRow) {
        activeRow.scrollIntoView({ block: 'nearest' });
    }
}

function switchToMotionIndex(index, reason = 'selected motion') {
    if (!currentMesh || motionFiles.length === 0) return;
    const normalizedIndex = ((Number(index) || 0) % motionFiles.length + motionFiles.length) % motionFiles.length;
    currentMotionIndex = normalizedIndex;
    playCurrentMotionWithFullReset(reason);
    scrollActiveMotionIntoView();
}

function playCurrentMotionWithFullReset(reason = 'motion start') {
    if (!currentMesh || motionFiles.length === 0) return;
    const requestToken = ++motionLoadToken;
    try {
        resetMotionRuntimeState(reason);
    } catch (error) {
        console.error("Hard motion reset failed:", error);
        reloadModel();
        return;
    }
    loadAndPlayMotion(currentMesh, {
        resetState: false,
        runtimeAlreadyReset: true,
        requestToken,
        reason
    });
}

function updateMotionDebugState() {
    window.__mmdTryDebug = {
        currentMotionIndex,
        motionFilesLength: motionFiles.length,
        motionName: getMotionDisplayName(motionEntries[currentMotionIndex] || {}, currentMotionIndex),
        checkedMotionCount: getCheckedMotionEntries().length,
        annotatedMotionCount: Object.keys(motionAnnotations).length,
        lastExportResult,
        motionLoadToken,
        activeMotionToken,
        motionStatus,
        motionStatusChangedAt
    };
}

function initDraggableModel() {
    const container = document.getElementById('draggable-model');
    if (!container) return;

    container.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        if (confirm("Reset current motion?")) {
            resetCurrentMotion();
        }
    });

    const dragBar = container.querySelector('.drag-bar');
    const handles = container.querySelectorAll('.resize-handle');

    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    if (dragBar) {
        dragBar.addEventListener('mousedown', (e) => {
            isDragging = true;
            dragOffsetX = e.clientX - container.offsetLeft;
            dragOffsetY = e.clientY - container.offsetTop;
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onDrag);
            document.addEventListener('mouseup', stopDrag);
        });
    }

    function onDrag(e) {
        if (!isDragging) return;
        container.style.left = (e.clientX - dragOffsetX) + 'px';
        container.style.top = (e.clientY - dragOffsetY) + 'px';
    }

    function stopDrag() {
        isDragging = false;
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', stopDrag);
    }

    let isResizing = false;
    let startX, startWidth, startHeight, startTop, startLeft;
    let aspectRatio = 300 / 350;
    let currentHandle = null;

    handles.forEach(handle => {
        handle.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            isResizing = true;
            currentHandle = handle;
            startX = e.clientX;
            startWidth = container.offsetWidth;
            startHeight = container.offsetHeight;
            startTop = container.offsetTop;
            startLeft = container.offsetLeft;
            aspectRatio = startWidth / startHeight;

            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onResize);
            document.addEventListener('mouseup', stopResize);
        });
    });

    function onResize(e) {
        if (!isResizing) return;

        const dx = e.clientX - startX;
        let newWidth = startWidth;
        let newHeight = startHeight;
        let newTop = startTop;
        let newLeft = startLeft;

        if (currentHandle.classList.contains('se')) {
            newWidth = startWidth + dx;
            newHeight = newWidth / aspectRatio;
        } else if (currentHandle.classList.contains('sw')) {
            newWidth = startWidth - dx;
            newHeight = newWidth / aspectRatio;
            newLeft = startLeft + (startWidth - newWidth);
        } else if (currentHandle.classList.contains('ne')) {
            newWidth = startWidth + dx;
            newHeight = newWidth / aspectRatio;
            newTop = startTop + (startHeight - newHeight);
        } else if (currentHandle.classList.contains('nw')) {
            newWidth = startWidth - dx;
            newHeight = newWidth / aspectRatio;
            newLeft = startLeft + (startWidth - newWidth);
            newTop = startTop + (startHeight - newHeight);
        }

        if (newWidth > 100) {
            container.style.width = newWidth + 'px';
            container.style.height = newHeight + 'px';
            if (newLeft !== startLeft) container.style.left = newLeft + 'px';
            if (newTop !== startTop) container.style.top = newTop + 'px';

            if (renderer && camera) {
                camera.aspect = newWidth / newHeight;
                camera.updateProjectionMatrix();
                renderer.setSize(newWidth, newHeight);
            }
        }
    }

    function stopResize() {
        isResizing = false;
        currentHandle = null;
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onResize);
        document.removeEventListener('mouseup', stopResize);
    }
}

const physicsParams = {
    type: 1,
    friction: 0.2,
    restitution: 0.05,
    collisionMargin: 0.002,
    damping: 0.95,
    rigidBodyType: 0
};

function createAnimationHelper() {
    return new THREE.MMDAnimationHelper({
        afterglow: 2.0,
        physics: true,
        unitStep: FIXED_STEP,
        maxStepNum: 3,
        gravity: new THREE.Vector3(0, -9.8 * 0.8, 0),
        worldScale: 1.0
    });
}

function recreateAnimationHelper() {
    if (physicsEnableTimer) {
        clearTimeout(physicsEnableTimer);
        physicsEnableTimer = null;
    }
    if (animationHelper) {
        try {
            animationHelper.enable('physics', false);
        } catch (e) {
            console.warn("Physics disable before helper reset failed", e);
        }
    }
    animationHelper = createAnimationHelper();
    lastPhysicsResetTime = Date.now();
    abnormalPhysicsFrames = 0;
    accumulator = 0;
    clock.getDelta();
}

function applyMmdRigidBodyStability(mesh) {
    const rigidBodies = mesh?.geometry?.userData?.MMD?.rigidBodies;
    if (!Array.isArray(rigidBodies)) return;

    rigidBodies.forEach((rb) => {
        rb.friction = physicsParams.friction;
        rb.restitution = physicsParams.restitution;
        rb.damping = physicsParams.damping;
        rb.positionDamping = physicsParams.damping;
        rb.rotationDamping = physicsParams.damping;

        const rbName = String(rb.name || "").toLowerCase();
        const isHair = rbName.includes("hair") || /\u9aea|\u524d\u9aea|\u5f8c\u9aea|\u6a2a\u9aea/i.test(rb.name || "");
        const isSkirt = rbName.includes("skirt") || /\u88d9|\u670d/i.test(rb.name || "");

        if (isHair) {
            rb.damping = 0.98;
            rb.rotationDamping = 0.98;
            rb.friction = 0.9;
            rb.mass = Math.max(0.01, Number(rb.mass || 0.01) * 2.0);
        } else if (isSkirt) {
            rb.damping = 0.9;
            rb.rotationDamping = 0.9;
        }
    });
    console.log("Optimized rigid body parameters");
}

function attachMeshToAnimationHelper(mesh) {
    if (!animationHelper || !mesh) return;
    const existingObject = getAnimationHelperObject(mesh);
    if (existingObject) return;
    applyMmdRigidBodyStability(mesh);
    animationHelper.add(mesh, {
        physics: true,
        ...physicsParams
    });
}

function getAnimationHelperObject(mesh = currentMesh) {
    if (!animationHelper || !mesh || !animationHelper.objects) return null;
    try {
        return animationHelper.objects.get(mesh) || null;
    } catch (e) {
        return null;
    }
}

function ensureMeshAttachedToAnimationHelper(mesh = currentMesh) {
    if (!mesh) return;
    if (!animationHelper) {
        recreateAnimationHelper();
    }
    if (!getAnimationHelperObject(mesh)) {
        attachMeshToAnimationHelper(mesh);
    }
}

function resetAttachedPhysics() {
    const helperObject = getAnimationHelperObject(currentMesh);
    const physics = helperObject && helperObject.physics;
    if (!physics || typeof physics.reset !== 'function') return false;

    physics.reset();
    return true;
}

function getFootPosition() {
    if (!currentMesh) return null;
    currentMesh.updateMatrixWorld(true);

    const footBones = currentMesh.skeleton.bones.filter(bone =>
        bone.name.toLowerCase().includes('leg') ||
        bone.name.toLowerCase().includes('foot') ||
        bone.name.toLowerCase().includes('\u8db3') ||
        bone.name.toLowerCase().includes('\u811a')
    );

    if (footBones.length === 0) {
        const box = new THREE.Box3().setFromObject(currentMesh);
        return new THREE.Vector3(
            currentMesh.position.x,
            box.min.y,
            currentMesh.position.z
        );
    }

    const footPosition = new THREE.Vector3();
    footBones.forEach(bone => {
        const worldPos = bone.getWorldPosition(new THREE.Vector3());
        footPosition.add(worldPos);
    });
    footPosition.divideScalar(footBones.length);

    return footPosition;
}

function forceResetPosition(options = {}) {
    if (currentMesh && initialPosition) {
        const hard = Boolean(options.hard);
        if (hard) {
            currentMesh.position.copy(initialPosition.position);
            currentMesh.rotation.copy(initialPosition.rotation);
            currentMesh.updateMatrixWorld(true);
            return;
        }

        const currentFootPos = getFootPosition();
        if (currentFootPos && initialFootPosition) {
            const offset = new THREE.Vector3().subVectors(initialFootPosition, currentFootPos);
            const yOffset = currentMesh.position.y - initialPosition.position.y;
            const maxYOffset = 2.0;
            if (Math.abs(yOffset) > maxYOffset) {
                const correctionSpeed = 0.1;
                const targetY = initialPosition.position.y + Math.sign(yOffset) * maxYOffset;
                currentMesh.position.y += (targetY - currentMesh.position.y) * correctionSpeed;
            }
            currentMesh.position.x += offset.x;
            currentMesh.position.z += offset.z;
            currentMesh.rotation.copy(initialPosition.rotation);
        }
    }
}

function isFiniteVector3(vector) {
    return vector && Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function detectPhysicsInstability(beforeY) {
    if (!currentMesh || !initialPosition) {
        abnormalPhysicsFrames = 0;
        return null;
    }

    const position = currentMesh.position;
    if (!isFiniteVector3(position) || !isFiniteVector3(initialPosition.position)) {
        abnormalPhysicsFrames += 1;
        return "non-finite model position";
    }

    const rootDrift = position.distanceTo(initialPosition.position);
    const frameYDelta = Math.abs(position.y - beforeY);
    let footDrift = 0;
    const footPosition = getFootPosition();
    if (footPosition && initialFootPosition && isFiniteVector3(footPosition) && isFiniteVector3(initialFootPosition)) {
        footDrift = footPosition.distanceTo(initialFootPosition);
    }

    const severe = rootDrift > PHYSICS_MAX_ROOT_DRIFT * 1.6
        || footDrift > PHYSICS_MAX_FOOT_DRIFT * 1.6
        || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA * 2;
    const abnormal = severe
        || rootDrift > PHYSICS_MAX_ROOT_DRIFT
        || footDrift > PHYSICS_MAX_FOOT_DRIFT
        || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA;

    if (!abnormal) {
        abnormalPhysicsFrames = 0;
        return null;
    }

    abnormalPhysicsFrames += 1;
    if (severe || abnormalPhysicsFrames >= 2) {
        return `root=${rootDrift.toFixed(2)}, foot=${footDrift.toFixed(2)}, yJump=${frameYDelta.toFixed(2)}`;
    }
    return null;
}

function resetMmdPhysics(reason, options = {}) {
    if (!currentMesh || !animationHelper) return false;
    const now = Date.now();
    if (!options.force && now - lastPhysicsPanicTime < PHYSICS_PANIC_COOLDOWN) {
        return false;
    }

    lastPhysicsPanicTime = now;
    abnormalPhysicsFrames = 0;
    console.warn(`Resetting MMD physics: ${reason}`);

    if (physicsEnableTimer) {
        clearTimeout(physicsEnableTimer);
        physicsEnableTimer = null;
    }

    try {
        animationHelper.enable('physics', false);
    } catch (e) {
        console.warn("Physics disable during reset failed", e);
    }
    forceResetPosition({ hard: true });

    const meshSnapshot = currentMesh;
    physicsEnableTimer = setTimeout(() => {
        if (!meshSnapshot || currentMesh !== meshSnapshot) return;
        try {
            forceResetPosition({ hard: true });
            ensureMeshAttachedToAnimationHelper(meshSnapshot);
            resetAttachedPhysics();
            animationHelper.enable('physics', true);
            lastPhysicsResetTime = Date.now();
        } catch (e) {
            console.warn("Physics reset failed", e);
        } finally {
            physicsEnableTimer = null;
        }
    }, options.delayMs ?? 80);

    return true;
}

if (typeof Ammo === 'function') {
    Ammo().then(function() {
        console.log('Ammo.js initialized');
        init();
    }).catch(function(err) {
        console.error('Ammo.js initialization failed:', err);
        setLoading('Ammo.js failed', 'The model cannot be loaded.');
    });
} else {
    setLoading('Ammo.js failed', 'The model cannot be loaded.');
}

function removeActiveFinishedHandler() {
    if (mixer && activeFinishedHandler) {
        mixer.removeEventListener('finished', activeFinishedHandler);
    }
    activeFinishedHandler = null;
}

function clearMotionTimers(invalidate = false) {
    if (invalidate) activeMotionToken += 1;
    if (motionFinishTimer) {
        clearTimeout(motionFinishTimer);
        motionFinishTimer = null;
    }
    if (motionLoopTimer) {
        clearTimeout(motionLoopTimer);
        motionLoopTimer = null;
    }
    if (motionFallbackTimer) {
        clearTimeout(motionFallbackTimer);
        motionFallbackTimer = null;
    }
    removeActiveFinishedHandler();
    activeAction = null;
}

function stopMmdPhysicsNow() {
    if (physicsEnableTimer) {
        clearTimeout(physicsEnableTimer);
        physicsEnableTimer = null;
    }
    if (animationHelper) {
        try {
            animationHelper.enable('physics', false);
        } catch (e) {
            console.warn("Physics disable failed", e);
        }
    }
}

function resetMeshPoseToInitial() {
    if (!currentMesh) return;

    if (currentMesh.geometry) {
        currentMesh.geometry.animations = [];
    }

    if (Array.isArray(currentMesh.morphTargetInfluences)) {
        currentMesh.morphTargetInfluences.fill(0);
    }

    if (currentMesh.skeleton) {
        try {
            if (typeof currentMesh.pose === 'function') {
                currentMesh.pose();
            } else if (typeof currentMesh.skeleton.pose === 'function') {
                currentMesh.skeleton.pose();
            }
            currentMesh.skeleton.bones.forEach(bone => {
                bone.updateMatrix();
                bone.updateMatrixWorld(true);
            });
        } catch (e) {
            console.warn("Skeleton pose reset failed", e);
        }
    }

    forceResetPosition({ hard: true });
    currentMesh.updateMatrixWorld(true);
}

function resetMotionRuntimeState(reason = "motion reset") {
    if (!currentMesh) return;

    clearMotionTimers(true);
    stopMmdPhysicsNow();

    if (mixer) {
        try {
            mixer.stopAllAction();
            mixer.uncacheRoot(currentMesh);
        } catch (e) {
            console.warn("Mixer reset failed", e);
        }
        mixer = null;
    }

    resetMeshPoseToInitial();
    ensureMeshAttachedToAnimationHelper(currentMesh);
    try {
        resetAttachedPhysics();
    } catch (e) {
        console.warn("Attached physics reset failed", e);
    }
    mixer = new THREE.AnimationMixer(currentMesh);
    forceResetPosition({ hard: true });
    initialFootPosition = getFootPosition();
    abnormalPhysicsFrames = 0;
    lastPhysicsPanicTime = 0;
    lastPhysicsResetTime = Date.now();
    accumulator = 0;
    clock.getDelta();
    console.log(`Motion runtime reset: ${reason}`);
}

function scheduleMotionFallback(reason, requestToken, vmdFilePath, delayMs = 800) {
    if (motionFallbackTimer) {
        clearTimeout(motionFallbackTimer);
        motionFallbackTimer = null;
    }
    motionFallbackTimer = setTimeout(() => {
        motionFallbackTimer = null;
        if (
            requestToken !== motionLoadToken ||
            !currentMesh ||
            motionFiles[currentMotionIndex] !== vmdFilePath
        ) {
            return;
        }

        if (motionFiles.length > 1) {
            console.warn(`Skipping motion after playback fallback: ${reason}`, vmdFilePath);
            switchToMotionIndex(currentMotionIndex + 1, reason);
        } else {
            console.warn(`Retrying only motion after playback fallback: ${reason}`, vmdFilePath);
            playCurrentMotionWithFullReset(reason);
        }
    }, delayMs);
}

function resetModelColdStartState(options = {}) {
    const preserveMotionIndex = Boolean(options.preserveMotionIndex);
    const preserveMotionList = Boolean(options.preserveMotionList);
    const preserveVmdCache = Boolean(options.preserveVmdCache);
    const savedMotionIndex = currentMotionIndex;
    const savedMotionFiles = motionFiles;
    const savedMotionEntries = motionEntries;

    if (physicsEnableTimer) {
        clearTimeout(physicsEnableTimer);
        physicsEnableTimer = null;
    }
    clearMotionTimers(true);
    motionLoadToken += 1;
    if (!preserveVmdCache) {
        vmdCache.clear();
    }
    if (preserveMotionList) {
        motionFiles = savedMotionFiles;
        motionEntries = savedMotionEntries;
    } else {
        motionFiles = [];
        motionEntries = [];
    }
    currentMotionIndex = preserveMotionIndex
        ? Math.min(savedMotionIndex, Math.max(0, motionFiles.length - 1))
        : 0;
    lastPhysicsPanicTime = 0;
    abnormalPhysicsFrames = 0;
    lastPhysicsResetTime = Date.now();
    accumulator = 0;
    clock.getDelta();

    if (camera && controls) {
        controls.target.set(0, 0, 0);
        camera.position.set(0, 10, 30);
        controls.update();
        initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
    }
    updateMotionHud('Resetting');
}

function reloadModel() {
    if (!loader) return;
    console.log("Reloading model and keeping current motion...");
    resetModelColdStartState({
        preserveMotionIndex: true,
        preserveMotionList: true,
        preserveVmdCache: true
    });
    loadModel(false, { coldStart: true, preserveMotionIndex: true });
}

function resetCurrentMotion() {
    if (!currentMesh) {
        reloadModel();
        return;
    }
    playCurrentMotionWithFullReset('right-click reset');
}

function loadModel(isFirstLoad, options = {}) {
    const coldStart = Boolean(options.coldStart);
    const preserveMotionIndex = Boolean(options.preserveMotionIndex);
    const loadGeneration = ++modelLoadGeneration;
    setLoading('Loading model...', MODEL_URL);
    if (currentMesh) {
        scene.remove(currentMesh);
        if (animationHelper) {
            try {
                animationHelper.remove(currentMesh);
            } catch (e) {
                console.warn("Helper remove error", e);
            }
        }
        if (mixer) {
            clearMotionTimers(true);
            mixer.stopAllAction();
            mixer = null;
        }

        if (currentMesh.geometry) currentMesh.geometry.dispose();
        if (currentMesh.material) {
            if (Array.isArray(currentMesh.material)) {
                currentMesh.material.forEach(m => m.dispose());
            } else {
                currentMesh.material.dispose();
            }
        }
        currentMesh = null;
    }

    if (!isFirstLoad) {
        recreateAnimationHelper();
    }

    loader.load(
        MODEL_URL,
        function(mesh) {
            if (loadGeneration !== modelLoadGeneration) {
                if (mesh.geometry) mesh.geometry.dispose();
                if (mesh.material) {
                    if (Array.isArray(mesh.material)) {
                        mesh.material.forEach(m => m.dispose());
                    } else {
                        mesh.material.dispose();
                    }
                }
                return;
            }

            currentMesh = mesh;
            mesh.scale.set(1.2, 1.2, 1.2);
            mesh.position.y = -11;

            initialPosition = {
                position: mesh.position.clone(),
                rotation: mesh.rotation.clone()
            };

            if (mesh.material) {
                if (Array.isArray(mesh.material)) {
                    mesh.material.forEach(setupMaterial);
                } else {
                    setupMaterial(mesh.material);
                }
            } else if (mesh.materials) {
                mesh.materials.forEach(setupMaterial);
            }

            scene.add(mesh);
            mesh.updateMatrixWorld(true);
            initialFootPosition = getFootPosition();
            mixer = new THREE.AnimationMixer(mesh);
            attachMeshToAnimationHelper(mesh);

            if (isFirstLoad) {
                fetchMotionFiles({ resetIndex: true });
                animate();
            } else if (coldStart) {
                forceResetPosition({ hard: true });
                if (preserveMotionIndex && motionFiles.length > 0) {
                    updateMotionHud('Reset');
                    playCurrentMotionWithFullReset('right-click reload');
                } else {
                    fetchMotionFiles({ resetIndex: true });
                }
            } else {
                if (animationHelper) {
                    animationHelper.enable('physics', false);
                }
                setTimeout(() => {
                    forceResetPosition();
                    playCurrentMotionWithFullReset('model reload');
                }, 180);
            }
        },
        function(xhr) {
            const total = xhr.total || 0;
            const progress = total ? `${(xhr.loaded / total * 100).toFixed(1)}%` : `${xhr.loaded} bytes`;
            setLoading('Loading model...', progress);
        },
        function(err) {
            console.error('MMD Model load error:', err);
            setLoading('Model load failed', 'Check the MMD model path and server.');
        }
    );
}

function init() {
    const container = document.querySelector('.canvas-container');
    if (!container) {
        console.error("Canvas container not found!");
        return;
    }
    const width = container.clientWidth;
    const height = container.clientHeight;

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(45, width / height, 1, 2000);
    camera.position.set(0, 10, 30);

    renderer = new THREE.WebGLRenderer({
        canvas: document.getElementById('mmd-canvas'),
        alpha: true,
        antialias: true,
        logarithmicDepthBuffer: true,
        precision: 'highp'
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    mainAmbientLight = new THREE.AmbientLight(0xaaaaaa, BASE_AMBIENT);
    scene.add(mainAmbientLight);

    mainDirLight = new THREE.DirectionalLight(0xffffff, BASE_DIR);
    mainDirLight.position.set(0, 20, 10);
    scene.add(mainDirLight);

    scene.fog = new THREE.FogExp2(0x000000, 0.035);

    mainTopLight = new THREE.DirectionalLight(0xffffff, BASE_TOP);
    mainTopLight.position.set(0, 50, 0);
    scene.add(mainTopLight);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableKeys = false; // Disable keyboard panning so arrow keys work normally in input fields
    loader = new THREE.MMDLoader();

    initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
    animationHelper = createAnimationHelper();

    loadModel(true);
    window.addEventListener('resize', onWindowResize, false);
}

function onWindowResize() {
    const container = document.querySelector('.canvas-container');
    if (!container || !camera || !renderer) return;

    const width = container.clientWidth;
    const height = container.clientHeight;

    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
}

function setupMaterial(material) {
    material.side = THREE.FrontSide;
    material.transparent = true;
    material.opacity = 1.0;
    material.depthWrite = true;
    material.depthTest = true;
    material.flatShading = false;
    material.skinning = true;
    material.morphTargets = true;
    material.morphNormals = false;
    material.needsUpdate = true;
}

function animate() {
    requestAnimationFrame(animate);

    if (controls && mainAmbientLight && mainDirLight && mainTopLight && initialCameraDistance > 0) {
        const currentDist = camera.position.distanceTo(controls.target);
        let ratio = currentDist / initialCameraDistance;
        ratio = Math.max(0.2, Math.min(ratio, 3.0));

        mainAmbientLight.intensity = BASE_AMBIENT * ratio;
        mainDirLight.intensity = BASE_DIR * ratio;
        mainTopLight.intensity = BASE_TOP * ratio;
    }

    const delta = clock.getDelta() * motionSpeed;
    const beforeY = currentMesh ? currentMesh.position.y : 0;

    if (mixer && currentMesh) {
        mixer.update(delta);
    }

    forceResetPosition();
    const prePhysicsInstability = detectPhysicsInstability(beforeY);
    const physicsResetStarted = prePhysicsInstability
        ? resetMmdPhysics(`pre-update instability (${prePhysicsInstability})`)
        : false;

    const now = Date.now();
    if (!physicsResetStarted && animationHelper && (now - lastPhysicsResetTime > PHYSICS_RESET_INTERVAL)) {
        animationHelper.enable('physics', false);
        forceResetPosition({ hard: true });
        const helperSnapshot = animationHelper;
        if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
        physicsEnableTimer = setTimeout(() => {
            if (animationHelper && animationHelper === helperSnapshot) {
                forceResetPosition({ hard: true });
                animationHelper.enable('physics', true);
            }
            physicsEnableTimer = null;
        }, 50);
        lastPhysicsResetTime = now;
    }

    if (animationHelper && !physicsResetStarted) {
        try {
            animationHelper.update(delta);
            forceResetPosition();
            const postPhysicsInstability = detectPhysicsInstability(beforeY);
            if (postPhysicsInstability) {
                resetMmdPhysics(`post-update instability (${postPhysicsInstability})`);
            }
        } catch (error) {
            console.warn("Physics error", error);
            resetMmdPhysics(`physics exception: ${error.message || error}`, { force: true });
        }
    }

    controls.update();
    renderer.render(scene, camera);
}

async function fetchMotionFiles(options = {}) {
    const expectedGeneration = modelLoadGeneration;
    try {
        setLoading('Loading motions...', 'Reading motion/try');
        const response = await fetch('/get_try_vmd_files');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        if (expectedGeneration !== modelLoadGeneration) return;

        const entries = Array.isArray(data.motions)
            ? data.motions
            : (Array.isArray(data.vmd_files) ? data.vmd_files.map(url => ({ url })) : []);

        if (entries.length > 0) {
            motionEntries = entries;
            motionFiles = entries.map(entry => entry.url).filter(Boolean);
            if (options.resetIndex) {
                currentMotionIndex = 0;
            }
            setLoading('', '', true);
            renderMotionList();
            updateMotionHud('Ready');
            if (currentMesh) {
                playCurrentMotionWithFullReset('initial motion');
            }
        } else {
            motionEntries = [];
            motionFiles = [];
            renderMotionList();
            updateMotionHud('No VMD files');
            setLoading('No VMD files', 'D:\\Dear\\MMD\\motion\\try');
        }
    } catch (error) {
        console.error('Fetch motion files failed:', error);
        updateMotionHud('Motion load failed');
        setLoading('Motion load failed', error.message || String(error));
    }
}

function handleMotionFinished(playToken, action) {
    if (playToken !== activeMotionToken || action !== activeAction) return;
    const finishedMotionIndex = currentMotionIndex;
    const finishedMotionPath = motionFiles[finishedMotionIndex];
    const finishedLoadToken = motionLoadToken;
    if (motionFinishTimer) {
        clearTimeout(motionFinishTimer);
        motionFinishTimer = null;
    }
    removeActiveFinishedHandler();
    activeAction = null;
    updateMotionHud('Paused');

    if (!isLooping || !currentMesh) return;
    motionLoopTimer = setTimeout(() => {
        if (
            !currentMesh ||
            finishedLoadToken !== motionLoadToken ||
            currentMotionIndex !== finishedMotionIndex ||
            motionFiles[currentMotionIndex] !== finishedMotionPath
        ) {
            return;
        }
        playCurrentMotionWithFullReset('same-motion loop');
    }, 1000);
}

function loadAndPlayMotion(mesh, options = {}) {
    if (!motionFiles || !Array.isArray(motionFiles) || motionFiles.length === 0) return;
    const expectedGeneration = modelLoadGeneration;
    const expectedMotionLoadToken = Number.isInteger(options.requestToken)
        ? options.requestToken
        : ++motionLoadToken;
    const resetState = options.resetState === true;

    const vmdFilePath = motionFiles[currentMotionIndex];
    console.log("Loading motion:", vmdFilePath);
    updateMotionHud('Loading');

    const isCurrentMotionRequest = () =>
        expectedGeneration === modelLoadGeneration &&
        expectedMotionLoadToken === motionLoadToken &&
        mesh === currentMesh &&
        motionFiles[currentMotionIndex] === vmdFilePath;

    const processAndPlayVmd = (vmd) => {
        if (!isCurrentMotionRequest()) return;
        if (vmd) {
            try {
                if (resetState) {
                    resetMotionRuntimeState(options.reason || 'motion start');
                    if (!isCurrentMotionRequest()) return;
                } else {
                    clearMotionTimers(!options.runtimeAlreadyReset);
                    stopMmdPhysicsNow();
                    if (mixer) {
                        mixer.stopAllAction();
                    } else {
                        mixer = new THREE.AnimationMixer(currentMesh);
                    }
                    resetMeshPoseToInitial();
                }

                if (!currentMesh.geometry.animations) {
                    currentMesh.geometry.animations = [];
                }

                if (vmd.tracks && vmd.tracks.length > 0) {
                    if (!vmd.isProcessed) {
                        vmd.tracks = vmd.tracks.filter(track => {
                            if (track.name.includes('.bones[')) {
                                if (track.name.includes('position')) {
                                    const values = track.values;
                                    for (let i = 1; i < values.length; i += 3) {
                                        const originalY = values[i];
                                        values[i] = Math.max(Math.min(originalY, 5), -5);
                                    }
                                }
                                return true;
                            }
                            return !track.name.includes('position') && !track.name.includes('quaternion') && !track.name.includes('.material');
                        });

                        let maxTime = 0;
                        vmd.tracks.forEach(track => {
                            if (track.times && track.times.length > 0) {
                                maxTime = Math.max(maxTime, track.times[track.times.length - 1]);
                            }
                        });
                        vmd.duration = maxTime > 0 ? maxTime : 5.0;
                        vmd.isProcessed = true;
                    }

                    if (animationHelper) {
                        animationHelper.enable('physics', false);
                    }

                    currentMesh.geometry.animations = [vmd];
                    const action = mixer.clipAction(vmd);
                    if (!action) {
                        console.warn("No playable action for VMD:", vmdFilePath);
                        updateMotionHud('No action');
                        scheduleMotionFallback('fallback after empty action', expectedMotionLoadToken, vmdFilePath);
                        return;
                    }

                    const playToken = ++activeMotionToken;
                    activeAction = action;
                    const durationSeconds = Math.max(0.2, Number(vmd.duration || action.getClip().duration || 5));
                    const fadeSeconds = Math.min(0.12, durationSeconds * 0.12);
                    if (typeof mixer.setTime === 'function') {
                        mixer.setTime(0);
                    } else {
                        mixer.time = 0;
                    }
                    action.reset();
                    action.enabled = true;
                    action.paused = false;
                    action.setEffectiveWeight(1);
                    action.setEffectiveTimeScale(1);
                    action.setLoop(THREE.LoopOnce, 1);
                    action.clampWhenFinished = true;
                    if (fadeSeconds > 0.01) {
                        action.fadeIn(fadeSeconds);
                    }
                    forceResetPosition({ hard: true });

                    activeFinishedHandler = function(event) {
                        if (event.action === action) {
                            handleMotionFinished(playToken, action);
                        }
                    };
                    mixer.addEventListener('finished', activeFinishedHandler);

                    motionFinishTimer = setTimeout(() => {
                        handleMotionFinished(playToken, action);
                    }, (durationSeconds / Math.max(0.05, motionSpeed)) * 1000 + 350);

                    action.play();
                    mixer.update(0);
                    updateMotionHud('Playing');

                    const helperSnapshot = animationHelper;
                    if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
                    physicsEnableTimer = setTimeout(() => {
                        if (animationHelper && animationHelper === helperSnapshot) {
                            animationHelper.enable('physics', true);
                        }
                        physicsEnableTimer = null;
                    }, 100);
                } else {
                    console.warn("VMD has no playable tracks:", vmdFilePath);
                    updateMotionHud('No tracks');
                    scheduleMotionFallback('fallback after empty VMD tracks', expectedMotionLoadToken, vmdFilePath);
                }
            } catch (error) {
                console.error("Error playing motion:", error);
                updateMotionHud('Play failed');
                scheduleMotionFallback('fallback after play failure', expectedMotionLoadToken, vmdFilePath);
            }
        }
    };

    if (vmdCache.has(vmdFilePath)) {
        processAndPlayVmd(vmdCache.get(vmdFilePath));
        return;
    }

    try {
        loader.loadAnimation(
            vmdFilePath,
            mesh,
            function(vmd) {
                if (!isCurrentMotionRequest()) return;
                vmdCache.set(vmdFilePath, vmd);
                processAndPlayVmd(vmd);
            },
            undefined,
            function(error) {
                if (!isCurrentMotionRequest()) return;
                console.error("VMD load failed:", error);
                updateMotionHud('VMD failed');
                if (isLooping && motionFiles.length > 1) {
                    scheduleMotionFallback('fallback after VMD load failure', expectedMotionLoadToken, vmdFilePath, 1000);
                }
            }
        );
    } catch (error) {
        console.error("Load animation error:", error);
        updateMotionHud('Load failed');
        scheduleMotionFallback('fallback after load exception', expectedMotionLoadToken, vmdFilePath);
    }
}

function playNextMotion() {
    if (!currentMesh || motionFiles.length === 0) return;
    switchToMotionIndex(currentMotionIndex + 1, 'next motion');
}
