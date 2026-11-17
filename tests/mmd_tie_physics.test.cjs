const test = require('node:test');
const assert = require('node:assert/strict');
const physics = require('../static/js/mmd-tie-physics.js');

test('animation and physics advance the same amount at different display refresh rates', () => {
    for (const mode of ['stable', 'fine']) {
        const hz = physics.getProfile(mode).hz;
        for (const fps of [30, 60, 120, 144, 240]) {
            const stepper = physics.createStepper(mode);
            let time = 0;
            for (let frame = 0; frame < fps * 3; frame++) {
                stepper.advance(1 / fps, delta => { time += delta; });
            }
            assert.equal(stepper.stats.steps, hz * 3, `${mode}, ${fps} fps`);
            assert.ok(Math.abs(time - 3) < 1e-9);
        }
    }
});

test('pause recovery and slow frames cannot inject a large simulation step', () => {
    const stepper = physics.createStepper('stable');
    const deltas = [];
    stepper.advance(0.1, delta => deltas.push(delta));
    assert.equal(deltas.length, 8);
    assert.ok(deltas.every(delta => delta === 1 / 120));
    assert.equal(stepper.advance(5, () => assert.fail('background catch-up')), 0);
    assert.equal(stepper.advance(1 / 120, () => {}), 1);
    assert.ok(stepper.stats.droppedSeconds >= 5);
    stepper.advance(1 / 240, () => assert.fail('half step'));
    stepper.reset();
    assert.equal(stepper.advance(1 / 240, () => assert.fail('stale accumulator')), 0);
});

test('contact experiment isolates supports while preserving hand contact and authored mass', () => {
    const tie = { name: 'Tie_1', type: 1, groupIndex: 7, groupTarget: 1 << 15,
        weight: 0.1, positionDamping: 0.999, rotationDamping: 0.999, friction: 0.5 };
    const support = { name: 'ネクタイ用2', type: 0, groupIndex: 15, groupTarget: 65535,
        weight: 1, positionDamping: 0.5, rotationDamping: 0.5, friction: 0.5 };
    const hand = { ...support, name: '左手首' };
    const bodies = [tie, support, hand];
    const original = structuredClone(bodies);
    physics.tuneRigidBodies(bodies, 'legacy');
    assert.equal(tie.positionDamping, 0.995);
    assert.equal(tie.weight, 0.1);
    physics.tuneRigidBodies(bodies, 'stable');
    assert.deepEqual(bodies, original);
    physics.tuneRigidBodies(bodies, 'contact-off');
    assert.equal(support.groupTarget & (1 << tie.groupIndex), 0);
    assert.ok(hand.groupTarget & (1 << tie.groupIndex));
    assert.equal(tie.groupTarget, original[0].groupTarget);
    assert.equal(support.groupTarget | (1 << tie.groupIndex), original[1].groupTarget);
    physics.tuneRigidBodies(bodies, 'stable');
    physics.tuneRigidBodies(bodies, 'stable');
    assert.deepEqual(bodies, original);
});

test('missing Ammo solver controls are reported without aborting model loading', () => {
    const result = physics.configurePhysics({ world: {}, unitStep: 1 / 120,
        maxStepNum: 3, gravity: { toArray: () => [0, -98, 0] } }, 'stable');
    assert.equal(result.iterationsSupported, false);
    assert.equal(result.solverIterations, null);
    assert.equal(physics.normalizeMode('__proto__'), 'stable');
});
