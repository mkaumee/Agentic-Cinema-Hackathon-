/**
 * One decision per prop.
 *
 * The agent writes to several suppliers about the same item, so three
 * negotiations can sit at READY_FOR_HUMAN for one cup. `purchase_orders` is
 * created with `create()` keyed by the *item*, so approving any one of them is
 * final and the rest become unapprovable — which makes listing them as three
 * separate decisions with three Approve buttons an invitation to spend more
 * than necessary on a screen that looks like it is offering a choice.
 */

import { describe, expect, it } from "vitest";

import { decisionsFor, questionsFor } from "../src/chat/decisions";
import type { Item, Negotiation } from "../src/hooks/useProject";

const item = (id: string, over: Partial<Item> = {}): Item => ({
  id,
  name: id,
  ...over,
});

const quote = (
  id: string,
  itemId: string,
  amount: number,
  over: Partial<Negotiation> = {},
): Negotiation => ({
  id,
  item_id: itemId,
  supplier_id: `s-${id}`,
  state: "READY_FOR_HUMAN",
  latest_quote: { unit_price: { amount, currency: "MYR" } },
  ...over,
});

describe("decisionsFor", () => {
  it("collapses rival quotes for one prop into one decision", () => {
    const out = decisionsFor(
      [item("cup")],
      [quote("a", "cup", 1439), quote("b", "cup", 719), quote("c", "cup", 900)],
    );

    expect(out).toHaveLength(1);
    expect(out[0]?.chosen.id).toBe("b");
    expect(out[0]?.rivals.map((r) => r.id)).toEqual(["c", "a"]);
  });

  it("counts props, not negotiations", () => {
    // The number the rail shows. Eight negotiations across four props is four
    // things a producer has to decide, and saying eight is simply wrong.
    const items = [item("cup"), item("mirror")];
    const negotiations = [
      quote("a", "cup", 719),
      quote("b", "cup", 1439),
      quote("c", "mirror", 719),
      quote("d", "mirror", 1439),
    ];

    expect(decisionsFor(items, negotiations)).toHaveLength(2);
  });

  it("ignores negotiations the agent has not stopped on", () => {
    // READY_FOR_HUMAN is the stop condition and the only thing that counts as
    // needing a person. A queue that also collects merely-interesting rows
    // trains people to ignore it.
    const out = decisionsFor(
      [item("cup")],
      [
        quote("a", "cup", 719, { state: "AWAITING_REPLY" }),
        quote("b", "cup", 900, { state: "DEAD" }),
      ],
    );

    expect(out).toEqual([]);
  });

  it("drops an item that is already ordered", () => {
    const out = decisionsFor(
      [item("cup", { status: "ORDERED" })],
      [quote("a", "cup", 719)],
    );

    expect(out).toEqual([]);
  });

  it("drops a negotiation pointing at an item this project does not have", () => {
    expect(decisionsFor([], [quote("a", "ghost", 719)])).toEqual([]);
  });

  it("puts a supplier who never named a price behind one who did", () => {
    // No quote must never win on price by being absent from the comparison.
    const out = decisionsFor(
      [item("cup")],
      [quote("silent", "cup", 0, { latest_quote: undefined }), quote("a", "cup", 900)],
    );

    expect(out[0]?.chosen.id).toBe("a");
  });

  it("is stable in prop order", () => {
    const out = decisionsFor(
      [item("mirror"), item("cup")],
      [quote("a", "mirror", 719), quote("b", "cup", 719)],
    );

    expect(out.map((d) => d.item.id)).toEqual(["cup", "mirror"]);
  });
});

describe("questionsFor", () => {
  const asked = (
    id: string,
    itemId: string,
    notes: string,
    over: Partial<Negotiation> = {},
  ): Negotiation => ({
    id,
    item_id: itemId,
    supplier_id: `s-${id}`,
    state: "READY_FOR_HUMAN",
    escalation_reason: "NEEDS_FROM_PRODUCER",
    latest_reasoning: notes,
    ...over,
  });

  it("keeps a seller's question out of the purchase decisions", () => {
    // The one that matters. A question has no quote, so left in `decisionsFor`
    // it would sort last — and where it is the only negotiation for its prop it
    // becomes `chosen`, putting an Approve button under "which mirror do you
    // mean?" at a price nobody named.
    const out = decisionsFor(
      [item("mirror")],
      [asked("a", "mirror", "Which mirror — the tall one?")],
    );

    expect(out).toEqual([]);
  });

  it("does not demote a real quote to a rival of a question", () => {
    const out = decisionsFor(
      [item("mirror")],
      [asked("a", "mirror", "Got a photo?"), quote("b", "mirror", 719)],
    );

    expect(out).toHaveLength(1);
    expect(out[0]?.chosen.id).toBe("b");
    expect(out[0]?.rivals).toEqual([]);
  });

  it("picks the questions up, with what was asked", () => {
    const out = questionsFor(
      [item("mirror")],
      [asked("a", "mirror", "Which mirror — the tall one?"), quote("b", "mirror", 719)],
    );

    expect(out).toHaveLength(1);
    expect(out[0]?.negotiation.id).toBe("a");
    expect(out[0]?.asked).toBe("Which mirror — the tall one?");
  });

  it("keeps one card per seller, not per prop", () => {
    // Two sellers asking about the same mirror have asked two different
    // questions. Grouping them the way decisions are grouped would answer one
    // and silently drop the other.
    const out = questionsFor(
      [item("mirror")],
      [asked("a", "mirror", "How tall?"), asked("b", "mirror", "What finish?")],
    );

    expect(out.map((q) => q.negotiation.id)).toEqual(["a", "b"]);
  });

  it("drops a question about a prop that is already ordered", () => {
    const out = questionsFor(
      [item("mirror", { status: "ORDERED" })],
      [asked("a", "mirror", "How tall?")],
    );

    expect(out).toEqual([]);
  });
});

describe("shop listings", () => {
  const listing = (
    id: string,
    itemId: string,
    amount: number,
    over: Partial<Negotiation> = {},
  ): Negotiation => ({
    id,
    item_id: itemId,
    supplier_id: `s-${id}`,
    state: "READY_FOR_HUMAN",
    listing_url: `https://shop.example.invalid/${id}`,
    escalation_reason: "LISTING_FOUND",
    first_quote: { unit_price: { amount, currency: "MYR" } },
    latest_quote: { unit_price: { amount, currency: "MYR" } },
    ...over,
  });

  it("marks a decision that came off a shop page", () => {
    const [decision] = decisionsFor([item("mug")], [listing("a", "mug", 89)]);

    expect(decision?.isListing).toBe(true);
  });

  it("does not mark a negotiated quote as one", () => {
    const [decision] = decisionsFor([item("mirror")], [quote("b", "mirror", 719)]);

    expect(decision?.isListing).toBe(false);
  });

  it("still lets the cheapest win when a prop has both", () => {
    // A listing and a negotiation for one prop are one decision, because the
    // purchase order is keyed by the item — approving either settles both.
    const out = decisionsFor(
      [item("mirror")],
      [quote("b", "mirror", 719), listing("a", "mirror", 89)],
    );

    expect(out).toHaveLength(1);
    expect(out[0]?.chosen.id).toBe("a");
    expect(out[0]?.isListing).toBe(true);
    expect(out[0]?.rivals.map((r) => r.id)).toEqual(["b"]);
  });

  it("reads the flag off the chosen one, not off any of them", () => {
    // The dearer listing is a rival here, so the card must render as a
    // negotiation — the button it shows acts on `chosen` and nothing else.
    const out = decisionsFor(
      [item("mirror")],
      [quote("b", "mirror", 89), listing("a", "mirror", 719)],
    );

    expect(out[0]?.chosen.id).toBe("b");
    expect(out[0]?.isListing).toBe(false);
  });
});
