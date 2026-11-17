// Isolated real-model check; no rendering, network access, or model file writes.
// node scripts/check_mmd_tie_physics.cjs <model.pmx> [mode] [motion.vmd] [fps]
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const lib = path.resolve(__dirname, '../static/libs/three-r106');
const tuning = require('../static/js/mmd-tie-physics.js');
global.THREE = require(path.join(lib, 'three.min.js'));
global.MMDParser = require(path.join(lib, 'mmdparser.min.js'));
for (const name of ['MMDLoader', 'MMDPhysics', 'CCDIKSolver', 'MMDAnimationHelper']) {
    require(path.join(lib, `${name}.js`));
}
const arrayBuffer = file => {
    const bytes = fs.readFileSync(file);
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
};

(async () => {
    const [modelFile, mode = 'stable', motionFile, fpsArgument = '60'] = process.argv.slice(2);
    if (!modelFile) throw new Error('Pass the local PMX file path.');
    global.Ammo = await require(path.join(lib, 'ammo.js'))();
    const profile = tuning.getProfile(mode);
    const parser = new MMDParser.Parser();
    const loader = new THREE.MMDLoader();
    // Keep the real geometry/bones/constraints; textures are unnecessary for a physics check.
    loader.meshBuilder.materialBuilder.build = () => new THREE.MeshBasicMaterial();
    const mesh = loader.meshBuilder.build(parser.parsePmx(arrayBuffer(modelFile), true), '');
    const bodies = mesh.geometry.userData.MMD.rigidBodies;
    for (const body of bodies) {
        const name = `${body.name || ''} ${body.englishName || ''}`;
        if (/スカート|skirt/i.test(name)) body.groupTarget = 0;
        if (/breast|bust|胸/i.test(name)) { body.type = 0; body.groupTarget = 0; }
    }
    const bodyReport = tuning.tuneRigidBodies(bodies, mode);
    assert.equal(bodyReport.tieCount, 7);
    assert.equal(bodyReport.supportCount, 3);
    const mixer = new THREE.AnimationMixer(mesh);
    let action = null;
    if (motionFile && motionFile !== '-') {
        const clip = loader.animationBuilder.build(parser.parseVmd(arrayBuffer(motionFile), true), mesh);
        action = mixer.clipAction(clip);
    }
    mesh.scale.setScalar(1.2);
    mesh.position.y = -13;
    new THREE.Scene().add(mesh);
    const helper = new THREE.MMDAnimationHelper();
    helper.add(mesh, { physics: true, unitStep: profile.unitStep, maxStepNum: profile.maxStepNum,
        gravity: new THREE.Vector3(...profile.gravity), warmup: 0 });
    const physics = helper.objects.get(mesh).physics;
    const runtime = tuning.configurePhysics(physics, mode);
    assert.equal(runtime.unitStep, profile.unitStep);
    assert.deepEqual(runtime.gravity, [0, -98, 0]);
    assert.equal(runtime.solverIterations, profile.iterations);
    for (const body of physics.bodies.filter(body => /^Tie_\d+$/.test(body.params.name))) {
        assert.ok(Math.abs(body.params.weight - 0.1) < 1e-6);
        assert.ok(Math.abs(body.body.getLinearDamping() - (mode === 'legacy' ? 0.995 : 0.999)) < 1e-6);
    }
    if (profile.hz) {
        for (let i = 0; i < profile.hz; i++) physics.update(profile.unitStep);
    } else physics.warmup(60);
    if (action) action.play();
    const stepper = tuning.createStepper(mode);
    const fps = Number(fpsArgument);
    const tie = physics.bodies.find(body => body.params.name === 'Tie_7').body;
    let previous = null;
    let squaredDistance = 0;
    let sampleCount = 0;
    let maxSpeed = 0;
    let maxTorsoRotation = 0;
    const started = performance.now();
    for (let frame = 0; frame < fps * 10; frame++) {
        stepper.advance(1 / fps, delta => {
            mixer.update(delta);
            const torso = mesh.skeleton.bones.find(bone => bone.name === '上半身');
            maxTorsoRotation = Math.max(maxTorsoRotation, Math.abs(torso.quaternion.x));
            mesh.updateMatrixWorld(true);
            helper.update(delta);
            for (const bone of mesh.skeleton.bones) {
                assert.ok([...bone.position.toArray(), ...bone.quaternion.toArray()].every(Number.isFinite));
            }
        });
        const origin = tie.getCenterOfMassTransform().getOrigin();
        const position = [origin.x(), origin.y(), origin.z()];
        if (previous && frame >= fps * 5) {
            const distanceSquared = position.reduce((sum, value, i) => sum + (value - previous[i]) ** 2, 0);
            squaredDistance += distanceSquared;
            sampleCount++;
            maxSpeed = Math.max(maxSpeed, Math.sqrt(distanceSquared) * fps);
        }
        previous = position;
    }
    const tipRmsSpeed = Math.sqrt(squaredDistance / sampleCount) * fps;
    if ((!motionFile || motionFile === '-') && profile.id === 'stable') {
        assert.ok(tipRmsSpeed < 0.01, `Stationary tie still jittering: ${tipRmsSpeed}`);
    }
    console.log(JSON.stringify({ mode, fps, motion: motionFile && motionFile !== '-' ? path.basename(motionFile) : 'rest',
        runtime, steps: stepper.stats.steps, simulationMs: Math.round(performance.now() - started),
        lastFiveSeconds: { tipRmsSpeed, tipMaxSpeed: maxSpeed },
        finalTipPosition: previous, maxTorsoRotation, animationTime: action?.time ?? null, finiteBones: true }));
})().catch(error => { console.error(error); process.exitCode = 1; });
