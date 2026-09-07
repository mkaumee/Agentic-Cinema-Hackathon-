import { Timestamp } from "firebase/firestore";
import { expect, it } from "vitest";

import { draftRows } from "../src/chat/rows";
import type { Item } from "../src/hooks/useProject";

it("restores saved drafts without an upload response and removes confirmed items", () => {
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
  expect(draftRows("film", [
    { ...cup, status: "RESEARCHING" }, mirror,
  ])[0]?.props.map((prop) => prop.item_id)).toEqual(["mirror"]);
  expect(draftRows("film", [
    { ...cup, status: "RESEARCHING" },
    { ...mirror, status: "ABANDONED" },
  ])).toEqual([]);
  expect(draftRows("other-film", [cup])[0]?.id).not.toBe(restored[0]?.id);
});
