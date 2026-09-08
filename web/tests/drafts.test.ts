/**
 * The confirmation gate has to survive a reload.
 *
 * `draftRows` is what makes that true: the card is derived from the `DRAFT`
 * items in Firestore rather than remembered in React state, so closing the tab
 * costs nothing. These cases pin the behaviour a producer would notice — one
 * card and not two, the quantities they were shown, the route the agent
 * proposed, and the card emptying itself as items are confirmed.
 */
import { Timestamp } from "firebase/firestore";
import { expect, it } from "vitest";

import { draftRows, transcriptRows } from "../src/chat/rows";
import type { Row } from "../src/chat/rows";
import type { Item } from "../src/hooks/useProject";

const cup: Item = {
  id: "cup",
  name: "Cup",
  status: "DRAFT",
  qty: 3,
  consumable: true,
  mentions: [{ line: "He throws the cup." }],
  updated_at: Timestamp.fromMillis(1000),
};
const mirror: Item = { ...cup, id: "mirror", name: "Mirror" };

it("restores saved drafts without an upload response and removes confirmed items", () => {
  expect(draftRows("film", [])).toEqual([]);

  const restored = draftRows("film", [cup]);
  expect(restored).toHaveLength(1);
  expect(restored[0]?.props[0]).toMatchObject({
    item_id: "cup",
    qty: 3,
    consumable: true,
    lines: ["He throws the cup."],
  });
  expect(restored[0]?.at.getTime()).toBe(1000);
  expect(draftRows("film", [cup])).toEqual(restored);

  // Later snapshots add items to the same card, not a second upload card.
  const expanded = draftRows("film", [cup, mirror]);
  expect(expanded).toHaveLength(1);
  expect(expanded[0]?.id).toBe(restored[0]?.id);
  expect(expanded[0]?.props).toHaveLength(2);

  expect(
    draftRows("film", [{ ...cup, status: "RESEARCHING" }, mirror])[0]?.props.map(
      (prop) => prop.item_id,
    ),
  ).toEqual(["mirror"]);
  expect(
    draftRows("film", [
      { ...cup, status: "RESEARCHING" },
      { ...mirror, status: "ABANDONED" },
    ]),
  ).toEqual([]);

  expect(draftRows("other-film", [cup])[0]?.id).not.toBe(restored[0]?.id);
});

it("carries the route through, so a reload does not silently reset the decision", () => {
  // The agent proposed nothing, so the card offers the road that asks a person
  // first. Defaulting to BUY would send a producer to a shop for a prop that
  // has to be built.
  expect(draftRows("film", [cup])[0]?.props[0]?.route).toBe("NEGOTIATE");

  const birdcage: Item = { ...cup, id: "birdcage", route: "BUY" };
  expect(draftRows("film", [birdcage])[0]?.props[0]?.route).toBe("BUY");
});

it("does not invent a filename, because the item does not record one", () => {
  // `PropsPart` reads an empty name as "Unconfirmed props". A guessed filename
  // on the card would be a claim about which upload this came from that
  // nothing stored can support.
  expect(draftRows("film", [cup])[0]?.filename).toBe("");
});

it("puts the restored card in the transcript, not just in a function nobody calls", () => {
  // The wiring, not the builder. A card that is assembled correctly and then
  // left out of the list looks on screen exactly like a producer who has not
  // uploaded anything — which is the bug this whole port exists to fix, so it
  // gets a test of its own rather than being assumed from `draftRows` passing.
  const said: Row = {
    kind: "producer",
    id: "p:1",
    text: "Read kopitiam.pdf.",
    at: new Date(500),
  };

  const rows = transcriptRows("film", [cup], [[said]]);
  expect(rows.map((row) => row.kind)).toEqual(["producer", "props"]);

  // And it is genuinely derived: no draft items, no card.
  expect(transcriptRows("film", [], [[said]]).map((row) => row.kind)).toEqual([
    "producer",
  ]);
});
