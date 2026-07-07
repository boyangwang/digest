import { test } from "node:test";
import assert from "node:assert/strict";
import { enqueue, drain } from "../src/queue.js";

test("enqueue runs tasks for a key strictly in order", async () => {
  const order = [];
  const mk = (n, delay) => () =>
    new Promise((r) => setTimeout(() => { order.push(n); r(); }, delay));
  enqueue("k", mk(1, 30));
  enqueue("k", mk(2, 5));
  enqueue("k", mk(3, 1));
  await drain("k");
  assert.deepEqual(order, [1, 2, 3]); // FIFO despite decreasing delays
});

test("a failing task does not break the chain", async () => {
  const order = [];
  enqueue("k2", () => Promise.reject(new Error("boom")));
  enqueue("k2", () => { order.push("after"); return Promise.resolve(); });
  await drain("k2");
  assert.deepEqual(order, ["after"]);
});

test("different keys run independently", async () => {
  const order = [];
  enqueue("a", () => new Promise((r) => setTimeout(() => { order.push("a"); r(); }, 20)));
  enqueue("b", () => { order.push("b"); return Promise.resolve(); });
  await Promise.all([drain("a"), drain("b")]);
  assert.deepEqual(order.sort(), ["a", "b"]);
});
