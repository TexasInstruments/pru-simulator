"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSource = fs.readFileSync(
  path.join(__dirname, "../../ui/static/app.js"),
  "utf8",
);
const indexSource = fs.readFileSync(
  path.join(__dirname, "../../ui/static/index.html"),
  "utf8",
);

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} must be defined in app.js`);

  const open = source.indexOf("{", start);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let i = open; i < source.length; i++) {
    const ch = source[i];
    const next = source[i + 1];

    if (lineComment) {
      if (ch === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (ch === "*" && next === "/") {
        blockComment = false;
        i++;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && next === "/") {
      lineComment = true;
      i++;
      continue;
    }
    if (ch === "/" && next === "*") {
      blockComment = true;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "{") depth++;
    if (ch === "}" && --depth === 0) {
      return source.slice(start, i + 1);
    }
  }

  throw new Error(`Could not extract ${name}`);
}

const helperSource = extractFunction(appSource, "graphBuildDigitalBuckets");
const helperContext = {};
vm.runInNewContext(
  `${helperSource}\nthis.graphBuildDigitalBuckets = graphBuildDigitalBuckets;`,
  helperContext,
);
const graphBuildDigitalBuckets = helperContext.graphBuildDigitalBuckets;

const frameHelperSource = extractFunction(appSource, "graphFindNewestSsiFrame");
const frameHelperContext = {};
vm.runInNewContext(
  `${frameHelperSource}\nthis.graphFindNewestSsiFrame = graphFindNewestSsiFrame;`,
  frameHelperContext,
);
const graphFindNewestSsiFrame = frameHelperContext.graphFindNewestSsiFrame;

const resolutionHelperSource = extractFunction(appSource, "graphResolutionInfo");
const resolutionHelperContext = {};
vm.runInNewContext(
  `${resolutionHelperSource}\nthis.graphResolutionInfo = graphResolutionInfo;`,
  resolutionHelperContext,
);
const graphResolutionInfo = resolutionHelperContext.graphResolutionInfo;

const runGateSource = extractFunction(appSource, "canStartRunRequest");
const runGateContext = {};
vm.runInNewContext(
  `${runGateSource}\nthis.canStartRunRequest = canStartRunRequest;`,
  runGateContext,
);
const canStartRunRequest = runGateContext.canStartRunRequest;

const memoryEqualSource = extractFunction(appSource, "memoryBytesEqual");
const memoryEqualContext = {};
vm.runInNewContext(
  `${memoryEqualSource}\nthis.memoryBytesEqual = memoryBytesEqual;`,
  memoryEqualContext,
);
const memoryBytesEqual = memoryEqualContext.memoryBytesEqual;

const sourceVisibilitySource = extractFunction(appSource, "sourceLineNeedsScroll");
const sourceVisibilityContext = {};
vm.runInNewContext(
  `${sourceVisibilitySource}\nthis.sourceLineNeedsScroll = sourceLineNeedsScroll;`,
  sourceVisibilityContext,
);
const sourceLineNeedsScroll = sourceVisibilityContext.sourceLineNeedsScroll;

const wireKeySource = extractFunction(appSource, "wireListKey");
const wireKeyContext = {};
vm.runInNewContext(
  `${wireKeySource}\nthis.wireListKey = wireListKey;`,
  wireKeyContext,
);
const wireListKey = wireKeyContext.wireListKey;

function bucketAt(buckets, x) {
  const bucket = buckets.find(candidate => candidate.x === x);
  assert.ok(bucket, `expected a bucket at pixel ${x}`);
  return { ...bucket };
}

function testTwoEdgePulseInsideOnePixel() {
  const samples = [{ runStep: 0 }, { runStep: 10 }, { runStep: 20 }];
  const buckets = graphBuildDigitalBuckets(samples, [0, 1, 0], 0, 100, 4);

  assert.deepEqual(bucketAt(buckets, 0), {
    x: 0,
    enter: 0,
    exit: 0,
    sawHigh: true,
    sawLow: true,
  });
}

function testAdjacentSubPixelPulsesRemainVisible() {
  const samples = [
    { runStep: 0 },
    { runStep: 2 },
    { runStep: 4 },
    { runStep: 18 },
    { runStep: 20 },
  ];
  const buckets = graphBuildDigitalBuckets(samples, [0, 1, 0, 1, 0], 0, 40, 4);

  for (const x of [0, 1]) {
    assert.deepEqual(bucketAt(buckets, x), {
      x,
      enter: 0,
      exit: 0,
      sawHigh: true,
      sawLow: true,
    });
  }
}

function testZoomedOrdinaryWaveformKeepsLevelSequence() {
  const samples = [
    { runStep: 0 },
    { runStep: 10 },
    { runStep: 20 },
    { runStep: 30 },
  ];
  const buckets = graphBuildDigitalBuckets(samples, [0, 1, 0, 1], 0, 30, 30);

  assert.deepEqual(
    Array.from(buckets, bucket => bucket.enter),
    [
      0, 0, 0, 0, 0,
      1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
      1, 1, 1, 1, 1,
    ],
  );
}

function testStepFallback() {
  const samples = [{ step: 0 }, { step: 10 }, { step: 20 }];
  const buckets = graphBuildDigitalBuckets(samples, [0, 1, 0], 0, 20, 20);

  assert.equal(bucketAt(buckets, 5).enter, 1);
  assert.equal(bucketAt(buckets, 15).enter, 0);
}

function testInputsAreNotMutated() {
  const samples = [
    { step: 0, runStep: 100, tag: "first" },
    { step: 10, runStep: 110, tag: "second" },
    { step: 20, runStep: 120, tag: "third" },
  ];
  const data = [0, 1, 0];
  const originalSamples = structuredClone(samples);
  const originalData = [...data];

  graphBuildDigitalBuckets(samples, data, 100, 120, 20);

  assert.deepEqual(samples, originalSamples);
  assert.deepEqual(data, originalData);
}

function makeClockLane(core, transitionTimes) {
  const samples = [{
    runStep: transitionTimes[0] - 4,
    step: transitionTimes[0] - 4,
    core,
    gpo: [1],
  }];
  let value = 1;
  for (const runStep of transitionTimes) {
    value = value ? 0 : 1;
    samples.push({ runStep, step: runStep, core, gpo: [value] });
  }
  return samples;
}

function frameTimes(start, count, halfPeriod = 4) {
  return Array.from({ length: count }, (_, i) => start + i * halfPeriod);
}

function plainFrame(frame) {
  return frame && { minStep: frame.minStep, maxStep: frame.maxStep };
}

function testFindsNewestCompleteFrame() {
  const samples = [
    ...makeClockLane("pru0", frameTimes(20, 24)),
    ...makeClockLane("pru0", frameTimes(200, 25)),
  ];

  assert.deepEqual(plainFrame(graphFindNewestSsiFrame(samples)), {
    minStep: 196,
    maxStep: 296,
  });
}

function testRejectsIncompleteFrame() {
  const samples = makeClockLane("pru0", frameTimes(100, 23));

  assert.equal(graphFindNewestSsiFrame(samples), null);
}

function testPrefersPru0ClockLane() {
  const samples = [
    ...makeClockLane("pru0", frameTimes(100, 25)),
    ...makeClockLane("pru1", frameTimes(300, 25)),
  ];

  assert.deepEqual(plainFrame(graphFindNewestSsiFrame(samples)), {
    minStep: 96,
    maxStep: 196,
  });
}

function testFallsBackWhenPru0IsAbsent() {
  const samples = makeClockLane("pru1", frameTimes(100, 25));

  assert.deepEqual(plainFrame(graphFindNewestSsiFrame(samples)), {
    minStep: 96,
    maxStep: 196,
  });
}

function testClampsFrameToBufferedBounds() {
  const samples = [
    { runStep: 0, step: 0, core: "pru0", gpo: [1] },
    ...makeClockLane("pru0", frameTimes(2, 24)).slice(1),
    { runStep: 95, step: 95, core: "pru0", gpo: [1] },
  ];

  assert.deepEqual(plainFrame(graphFindNewestSsiFrame(samples)), {
    minStep: 0,
    maxStep: 95,
  });
}

function testFrameInputsAreNotMutated() {
  const samples = makeClockLane("pru0", frameTimes(100, 25));
  const originalSamples = structuredClone(samples);

  graphFindNewestSsiFrame(samples);

  assert.deepEqual(samples, originalSamples);
}

function testFitFrameControlIsWired() {
  assert.match(indexSource, /id="graph-fit-frame-btn"/);
  assert.match(appSource, /graph-fit-frame-btn/);
  assert.match(appSource, /graphFindNewestSsiFrame\(graphGetSamples\(\)/);
  assert.match(appSource, /No complete SSI frame in capture/);
}

function testResolutionWarnsForSubPixelRuns() {
  const channels = [{
    samples: [{ runStep: 0 }, { runStep: 4 }, { runStep: 8 }, { runStep: 20 }],
    data: [0, 1, 0, 0],
  }];

  assert.deepEqual(
    { ...graphResolutionInfo(channels, 0, 100, 10) },
    { cyclesPerPixel: 10, subPixel: true },
  );
}

function testResolutionDoesNotTreatClippedFirstRunAsObserved() {
  const channels = [{
    samples: [{ runStep: 0 }, { runStep: 4 }, { runStep: 20 }],
    data: [0, 1, 0],
  }];

  assert.equal(graphResolutionInfo(channels, 0, 100, 10).subPixel, false);
}

function testResolutionDoesNotWarnWhenZoomedIn() {
  const channels = [{
    samples: [{ runStep: 0 }, { runStep: 4 }, { runStep: 20 }],
    data: [0, 1, 0],
  }];

  assert.deepEqual(
    { ...graphResolutionInfo(channels, 0, 10, 100) },
    { cyclesPerPixel: 0.1, subPixel: false },
  );
}

function testRunRequestGatePreventsBacklog() {
  assert.equal(canStartRunRequest(false), true);
  assert.equal(canStartRunRequest(true), false);
  assert.match(appSource, /type === "run_done"/);
}

function testUnchangedMemorySnapshotsAreRecognized() {
  assert.equal(memoryBytesEqual([], []), true);
  assert.equal(memoryBytesEqual([0, 1, 255], [0, 1, 255]), true);
  assert.equal(memoryBytesEqual([0, 1], [0, 2]), false);
  assert.equal(memoryBytesEqual([0], [0, 1]), false);
}

function testSourceScrollOnlyHappensOutsideTheViewport() {
  assert.equal(sourceLineNeedsScroll(20, 30, 0, 100), false);
  assert.equal(sourceLineNeedsScroll(-1, 10, 0, 100), true);
  assert.equal(sourceLineNeedsScroll(95, 105, 0, 100), true);
}

function testUnchangedWireListsHaveTheSameRenderKey() {
  const wires = [{ src_core: "pru1", src_pin: 0, dst_core: "pru0", dst_pin: 8 }];
  assert.equal(wireListKey(wires), wireListKey(structuredClone(wires)));
  assert.notEqual(
    wireListKey(wires),
    wireListKey([{ src_core: "pru1", src_pin: 1, dst_core: "pru0", dst_pin: 8 }]),
  );
}

testTwoEdgePulseInsideOnePixel();
testAdjacentSubPixelPulsesRemainVisible();
testZoomedOrdinaryWaveformKeepsLevelSequence();
testStepFallback();
testInputsAreNotMutated();
testFindsNewestCompleteFrame();
testRejectsIncompleteFrame();
testPrefersPru0ClockLane();
testFallsBackWhenPru0IsAbsent();
testClampsFrameToBufferedBounds();
testFrameInputsAreNotMutated();
testFitFrameControlIsWired();
testResolutionWarnsForSubPixelRuns();
testResolutionDoesNotTreatClippedFirstRunAsObserved();
testResolutionDoesNotWarnWhenZoomedIn();
testRunRequestGatePreventsBacklog();
testUnchangedMemorySnapshotsAreRecognized();
testSourceScrollOnlyHappensOutsideTheViewport();
testUnchangedWireListsHaveTheSameRenderKey();

console.log("signal graph helper tests passed");
