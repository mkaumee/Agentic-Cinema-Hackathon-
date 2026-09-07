/**
 * The other kind of decision: a shop page, not a conversation.
 *
 * Some props are negotiated — rentals, handmade pieces, period furniture,
 * animals. Somebody has to be written to, and it takes days. Others are six
 * coffee mugs, and doing that by email was faintly absurd: the answer is a
 * listing, a price and a link.
 *
 * Both stop at the same gate, and that is deliberate — `CLAUDE.md` is explicit
 * that a listing is just another quote and must funnel into the same approval,
 * so there is exactly one path to money. What differs is everything a card
 * would otherwise say. Nothing was negotiated here, so no rounds were spent,
 * nobody opened at a higher price and nobody was talked down. Printing those
 * fields would be inventing a conversation, and a producer who catches one
 * invention stops believing the numbers.
 *
 * There is also no **Push for 10% less**. Not hidden as a tidiness measure:
 * that button makes the negotiation due, and a row with no email address in
 * the tick's send path is an agent trying to haggle with a URL. The server
 * refuses it too — a hidden button is not a guard.
 *
 * Approving does not spend anything. It records the purchase order and opens
 * the shop; the producer pays on that site, and afterwards this card asks
 * whether it actually went through, because nothing here can know.
 */

import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useState } from "react";

import { approve, type Outcome } from "@/approvals";
import { confirmReceipt } from "@/chat/api";
import { useProjectId } from "@/components/chat/context";
import { Button } from "@/components/ui/button";

export interface ListingArgs {
  negotiationId?: string;
  itemId?: string;
  item?: string;
  shop?: string;
  price?: string;
  url?: string;
  rivals?: number;
}

/** Trim a URL to something a person can read and still recognise. */
export function shopLabel(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "") + parsed.pathname;
  } catch {
    // Not a URL we can parse. Show it as given rather than hiding it — the
    // producer is about to click it, so they should see what it is.
    return url;
  }
}

export function ListingPart({ args }: ToolCallMessagePartProps<ListingArgs, unknown>) {
  const projectId = useProjectId();
  return <Listing args={args} projectId={projectId} />;
}

export function Listing({
  args,
  projectId,
  compact = false,
}: {
  args: ListingArgs;
  projectId: string;
  /** The rail's version: same decision, less around it. */
  compact?: boolean;
}) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [answered, setAnswered] = useState("");
  const [note, setNote] = useState("");
  const [saying, setSaying] = useState(false);
  const url = args.url ?? "";

  const buy = () => {
    // Opened synchronously, before any await. A window.open inside a promise
    // callback is not a user gesture as far as the browser is concerned and
    // the popup blocker eats it silently — MailboxCard.tsx has the same note.
    if (url !== "") window.open(url, "_blank", "noopener,noreferrer");
    setBusy(true);
    void approve(projectId, args.itemId ?? "", args.negotiationId ?? "")
      .then(setOutcome)
      .finally(() => setBusy(false));
  };

  const report = (received: boolean) => {
    setSaying(true);
    void confirmReceipt(projectId, args.itemId ?? "", received, note)
      .then((result) => {
        setAnswered(
          result.kind === "done"
            ? received
              ? "Marked as bought."
              : "Marked as not bought. The order record stands — purchase " +
                "orders are never rewritten — so this one needs sorting by hand."
            : result.detail,
        );
      })
      .finally(() => setSaying(false));
  };

  return (
    <div className="my-2 rounded-lg border-2 border-foreground/40 px-4 py-3 text-sm">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Needs you · buy it yourself
      </p>
      <p className="mt-1 font-medium">
        <span className="capitalize">{args.item ?? "an item"}</span> —{" "}
        {args.shop ?? "a shop"} at {args.price ?? "no price"}
      </p>
      <p className="mt-1 text-muted-foreground">
        Nothing was negotiated. That is the price on the page.
      </p>
      {url !== "" && (
        // Shown as text as well as being a link, so it is possible to see where
        // the button goes before pressing it.
        <p className="mt-2 break-all text-xs">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-4"
          >
            {shopLabel(url)}
          </a>
        </p>
      )}
      {args.rivals !== undefined && args.rivals > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          {args.rivals} other listing{args.rivals === 1 ? "" : "s"} for this
          prop, all dearer. Approving settles them too.
        </p>
      )}

      {outcome === null ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button size="sm" loading={busy} onClick={buy}>
            {busy ? "Recording…" : `Buy at ${args.shop ?? "the shop"}`}
          </Button>
          <span className="text-xs text-muted-foreground">
            Opens the shop and records the order. You pay on their site.
          </span>
        </div>
      ) : (
        <Settled
          outcome={outcome}
          compact={compact}
          answered={answered}
          note={note}
          saying={saying}
          onNote={setNote}
          onReport={report}
        />
      )}
    </div>
  );
}

function Settled({
  outcome,
  compact,
  answered,
  note,
  saying,
  onNote,
  onReport,
}: {
  outcome: Outcome;
  compact: boolean;
  answered: string;
  note: string;
  saying: boolean;
  onNote: (value: string) => void;
  onReport: (received: boolean) => void;
}) {
  if (outcome.kind === "duplicate") {
    return (
      <p className="mt-3 text-muted-foreground">
        Refused: this prop is already ordered. {outcome.detail}
      </p>
    );
  }
  if (outcome.kind === "forbidden") {
    return (
      <p className="mt-3 text-destructive">
        You are not a producer on this deployment. {outcome.detail}
      </p>
    );
  }
  if (outcome.kind !== "approved") {
    return <p className="mt-3 text-destructive">{outcome.detail}</p>;
  }

  if (answered !== "") return <p className="mt-3 font-medium">{answered}</p>;

  return (
    <div className="mt-3">
      <p className="font-medium">
        Order recorded. Finish the checkout on the shop&rsquo;s site.
      </p>
      {!compact && (
        <p className="mt-1 text-xs text-muted-foreground">
          Nothing here can see whether that went through, so it has to ask.
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={saying} onClick={() => onReport(true)}>
          I bought it
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={saying}
          onClick={() => onReport(false)}
        >
          It didn&rsquo;t go through
        </Button>
      </div>
      <input
        value={note}
        disabled={saying}
        placeholder="What happened? (optional)"
        onChange={(e) => onNote(e.target.value)}
        className="mt-2 w-full rounded border bg-background px-2 py-1 text-xs"
      />
    </div>
  );
}
