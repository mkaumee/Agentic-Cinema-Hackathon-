/**
 * One decision per prop, not one per negotiation.
 *
 * The agent approaches several suppliers for the same item and can end up
 * holding two or three quotes that are all `READY_FOR_HUMAN`. Listing those as
 * separate decisions would be wrong twice over: it inflates the "needs you"
 * count against a producer who has four things to decide rather than eight,
 * and it puts an Approve button under the expensive quote sitting right beside
 * the cheap one. `purchase_orders` is keyed by item, so approving either is
 * final and the other becomes unapprovable — the screen must not make that
 * look like a choice between equals.
 *
 * Cheapest live quote wins and the rest become rivals, which is what the Inbox
 * has always done. This is that rule, lifted out so the rail, the transcript
 * and the Inbox cannot drift into three different counts of the same thing.
 *
 * Type-only imports here, deliberately: the shapes come from the hooks module
 * but nothing at runtime does, so this stays testable without Firebase.
 */

import type { Item, Negotiation } from "@/hooks/useProject";

export const WAITING = "READY_FOR_HUMAN";

/**
 * The escalation that is a question, not a purchase.
 *
 * A seller who asks for a reference photo parks the negotiation in the same
 * `READY_FOR_HUMAN` state a good quote does, and that is right — both need a
 * person. What they need from that person is not remotely the same, and the
 * two must never share a card: an Approve button under "which mirror do you
 * mean?" offers to buy at a price nobody named.
 */
export const QUESTION = "NEEDS_FROM_PRODUCER";

/** A seller's question, waiting on the one person who can answer it. */
export interface Question {
  item: Item;
  negotiation: Negotiation;
  /** What they asked, in their words — the brain puts it in `notes`, which
   * lands on the record as `latest_reasoning`. */
  asked: string;
}

export interface Decision {
  item: Item;
  /** The one an Approve button may act on. */
  chosen: Negotiation;
  /** Everyone else who quoted. Shown, never actionable. */
  rivals: Negotiation[];
}

const priceOf = (n: Negotiation): number =>
  n.latest_quote?.unit_price?.amount ?? Infinity;

export function decisionsFor(
  items: Item[],
  negotiations: Negotiation[],
): Decision[] {
  const byItem = new Map<string, Negotiation[]>();
  for (const n of negotiations) {
    if (n.state !== WAITING || n.item_id === undefined) continue;
    // A question is not a quote. Left in, it could be the only negotiation for
    // its prop and would therefore be `chosen` — putting an Approve button
    // under a seller asking which mirror we mean, at no price. It gets its own
    // card from `questionsFor`.
    if (n.escalation_reason === QUESTION) continue;
    byItem.set(n.item_id, [...(byItem.get(n.item_id) ?? []), n]);
  }

  const out: Decision[] = [];
  for (const [itemId, group] of byItem) {
    const item = items.find((i) => i.id === itemId);
    // An item already ordered has nothing left to decide, and an item this
    // project does not have is a negotiation pointing at nothing — neither
    // belongs in a queue a person is asked to work through.
    if (item === undefined || item.status === "ORDERED") continue;
    const sorted = [...group].sort((a, b) => priceOf(a) - priceOf(b));
    const [chosen, ...rivals] = sorted;
    if (chosen === undefined) continue;
    out.push({ item, chosen, rivals });
  }
  return out.sort((a, b) => (a.item.name ?? "").localeCompare(b.item.name ?? ""));
}

/**
 * Sellers who asked the producer something, one card each.
 *
 * Not grouped by item the way decisions are, and the difference is not an
 * oversight: two suppliers asking about the same prop have asked two different
 * questions and need two different answers. Grouping would silently drop one
 * of them.
 */
export function questionsFor(
  items: Item[],
  negotiations: Negotiation[],
): Question[] {
  const out: Question[] = [];
  for (const n of negotiations) {
    if (n.state !== WAITING || n.escalation_reason !== QUESTION) continue;
    const item = items.find((i) => i.id === n.item_id);
    if (item === undefined || item.status === "ORDERED") continue;
    out.push({ item, negotiation: n, asked: n.latest_reasoning ?? "" });
  }
  return out.sort((a, b) => (a.item.name ?? "").localeCompare(b.item.name ?? ""));
}
