import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  Award, BarChart3, Cake, EyeOff, Eye, MessageSquare, Newspaper, Pin, PinOff, Send, Settings2, Sparkles, Trash2, UserPlus, X, Plus,
} from "lucide-react";
import { PageHeader, Spinner, Empty } from "../../components/ui";
import { Modal, Field, inputCls, err } from "../canada/_shared";
import {
  feedApi, FeedItem, FeedFilterKind, FeedPage as FeedPageData, FeedComment, ReactionKey, REACTION_LABELS, KIND_LABELS, NewPost,
} from "../../api/feed";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const REACTIONS: ReactionKey[] = ["like", "celebrate", "support", "insightful"];
const CHIPS: (FeedFilterKind | "")[] = ["", "post", "poll", "celebrations", "kudos", "birthday", "anniversary", "new_joiner"];

function ago(iso: string | null) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString("en-CA", { day: "numeric", month: "short", year: "numeric" });
}

function initials(name?: string | null) {
  const p = (name || "?").trim().split(/\s+/);
  return ((p[0]?.[0] || "") + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase() || "?";
}

const KIND_META: Record<string, { icon: any; tone: string; label: string }> = {
  post: { icon: Newspaper, tone: "bg-primary-50 text-primary-600", label: "Post" },
  poll: { icon: BarChart3, tone: "bg-sky-50 text-sky-600", label: "Poll" },
  birthday: { icon: Cake, tone: "bg-pink-50 text-pink-600", label: "Birthday" },
  anniversary: { icon: Sparkles, tone: "bg-amber-50 text-amber-600", label: "Work anniversary" },
  new_joiner: { icon: UserPlus, tone: "bg-emerald-50 text-emerald-600", label: "New joiner" },
  kudos: { icon: Award, tone: "bg-purple-50 text-purple-600", label: "Kudos" },
};

function useFeedKey(filters: { department: string; location: string; kind: string }) {
  return ["engage-feed", filters.department, filters.location, filters.kind];
}

// ══════════════════════════ page ══════════════════════════════════════════════

export default function FeedPage() {
  const [filters, setFilters] = useState({ department: "", location: "", kind: "" as FeedFilterKind | "" });
  const [openComments, setOpenComments] = useState<FeedItem | null>(null);
  const [hideTarget, setHideTarget] = useState<{ kind: "post" | "comment"; id: string; itemId: string } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const qc = useQueryClient();
  const key = useFeedKey(filters);

  const feed = useInfiniteQuery({
    queryKey: key,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => feedApi.list({ ...filters, limit: 20, before: pageParam }),
    getNextPageParam: (last: FeedPageData) => last.next_before,
  });
  const first = feed.data?.pages[0];
  const items = useMemo(() => {
    const seen = new Set<string>();
    return (feed.data?.pages || []).flatMap((p) => p.items).filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true)));
  }, [feed.data]);
  const canModerate = !!first?.can_moderate;
  const refresh = () => qc.invalidateQueries({ queryKey: ["engage-feed"] });

  const patchItem = (id: string, fn: (i: FeedItem) => FeedItem) =>
    qc.setQueryData(key, (old: any) => old && ({ ...old, pages: old.pages.map((p: FeedPageData) => ({ ...p, items: p.items.map((i) => (i.id === id ? fn(i) : i)) })) }));

  const react = useMutation({
    mutationFn: ({ id, emoji }: { id: string; emoji: ReactionKey }) => feedApi.react(id, emoji),
    onSuccess: (r) => patchItem(r.item_id, (i) => ({ ...i, reactions: r.reactions })),
    onError: (e) => toast.error(err(e, "Could not react")),
  });
  const vote = useMutation({
    mutationFn: ({ id, option }: { id: string; option: string }) => feedApi.vote(id, option),
    onSuccess: (r) => { patchItem(r.item_id, (i) => ({ ...i, poll: r.poll })); toast.success("Vote recorded"); },
    onError: (e) => toast.error(err(e, "Could not vote")),
  });
  const moderate = useMutation({
    mutationFn: ({ id, action, reason }: { id: string; action: "hide" | "unhide" | "pin" | "unpin"; reason?: string }) => feedApi.moderate(id, action, reason),
    onSuccess: (_r, v) => { toast.success({ hide: "Hidden from employees", unhide: "Visible again", pin: "Pinned to the top", unpin: "Unpinned" }[v.action]); refresh(); },
    onError: (e) => toast.error(err(e, "Could not update the post")),
  });
  const remove = useMutation({
    mutationFn: (id: string) => feedApi.remove(id),
    onSuccess: () => { toast.success("Post deleted"); refresh(); },
    onError: (e) => toast.error(err(e, "Could not delete")),
  });

  return (
    <div className="max-w-6xl mx-auto">
      <PageHeader title="Company feed" subtitle="News, polls, celebrations and kudos from across the company" icon={Newspaper}
        actions={<button type="button" className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => setShowSettings((s) => !s)}>
          <Settings2 className="w-4 h-4" /><span className="hidden sm:inline">My feed settings</span></button>} />

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_300px] gap-4 items-start">
        <div className="space-y-3 min-w-0 order-2 lg:order-1">
          <Composer departments={first?.departments || []} locations={first?.locations || []} canModerate={canModerate}
            me={first?.me} onPosted={refresh} />

          <div className="flex gap-1.5 overflow-x-auto pb-1 -mx-1 px-1">
            {CHIPS.map((k) => (
              <button key={k || "all"} type="button" onClick={() => setFilters((f) => ({ ...f, kind: k }))}
                className={`whitespace-nowrap text-xs px-3 py-1.5 rounded-full border transition ${filters.kind === k
                  ? "bg-primary-600 text-white border-primary-600" : "bg-white text-gray-600 border-surface-border hover:border-primary-300"}`}>
                {k ? KIND_LABELS[k] : "Everything"}
              </button>
            ))}
          </div>

          {feed.isLoading ? <div className="flex justify-center py-12"><Spinner /></div>
            : feed.isError ? <div className="card text-sm text-red-600">{err(feed.error, "Could not load the feed")}</div>
            : items.length === 0 ? <div className="card"><Empty>Nothing here yet — be the first to post.</Empty></div>
            : items.map((it) => (
              <FeedCard key={it.id} item={it} busy={react.isPending || vote.isPending}
                onReact={(emoji) => react.mutate({ id: it.id, emoji })}
                onVote={(option) => vote.mutate({ id: it.id, option })}
                onComments={() => setOpenComments(it)}
                onPin={() => moderate.mutate({ id: it.id, action: it.pinned ? "unpin" : "pin" })}
                onHide={() => (it.hidden ? moderate.mutate({ id: it.id, action: "unhide" }) : setHideTarget({ kind: "post", id: it.id, itemId: it.id }))}
                onDelete={() => { if (window.confirm("Delete this post? This cannot be undone.")) remove.mutate(it.id); }} />
            ))}

          {feed.hasNextPage && (
            <div className="flex justify-center">
              <button type="button" className="btn-secondary text-xs" disabled={feed.isFetchingNextPage} onClick={() => feed.fetchNextPage()}>
                {feed.isFetchingNextPage ? "Loading…" : "Show older"}
              </button>
            </div>
          )}
        </div>

        <aside className="space-y-3 order-1 lg:order-2 lg:sticky lg:top-4">
          {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} onSaved={refresh} />}
          <div className="card space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Filter</h3>
            <div className="grid grid-cols-2 lg:grid-cols-1 gap-2">
              <Field label="Department">
                <select className={inputCls} value={filters.department} onChange={(e) => setFilters((f) => ({ ...f, department: e.target.value }))}>
                  <option value="">All departments</option>
                  {(first?.departments || []).map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </Field>
              <Field label="Location">
                <select className={inputCls} value={filters.location} onChange={(e) => setFilters((f) => ({ ...f, location: e.target.value }))}>
                  <option value="">All locations</option>
                  {(first?.locations || []).map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </Field>
            </div>
            {(filters.department || filters.location || filters.kind) && (
              <button type="button" className="text-xs text-primary-600 hover:underline" onClick={() => setFilters({ department: "", location: "", kind: "" })}>Clear filters</button>
            )}
          </div>
          {canModerate && (
            <div className="card text-xs text-gray-600 space-y-1 hidden lg:block">
              <p className="font-semibold text-gray-700">You are moderating</p>
              <p>Pin a post to keep it on top. Hide a post or comment with a reason — the author is told why. Hidden items stay visible to HR only.</p>
            </div>
          )}
        </aside>
      </div>

      {openComments && <CommentsDrawer item={openComments} canModerate={canModerate} onClose={() => setOpenComments(null)}
        onHide={(cid) => setHideTarget({ kind: "comment", id: cid, itemId: openComments.id })} onChanged={refresh} />}
      {hideTarget && <HideModal target={hideTarget} onClose={() => setHideTarget(null)} onDone={() => {
        setHideTarget(null); refresh(); qc.invalidateQueries({ queryKey: ["feed-comments", hideTarget.itemId] });
      }} />}
    </div>
  );
}

// ══════════════════════════ composer ══════════════════════════════════════════

function Composer({ departments, locations, canModerate, me, onPosted }:
  { departments: string[]; locations: string[]; canModerate: boolean; me?: { department: string | null; location: string | null }; onPosted: () => void }) {
  const [kind, setKind] = useState<"post" | "poll">("post");
  const [body, setBody] = useState("");
  const [options, setOptions] = useState<string[]>(["", ""]);
  const [closes, setCloses] = useState("");
  const [aDept, setADept] = useState("");
  const [aLoc, setALoc] = useState("");
  const [open, setOpen] = useState(false);

  const create = useMutation({
    mutationFn: (p: NewPost) => feedApi.create(p),
    onSuccess: () => {
      toast.success(kind === "poll" ? "Poll posted" : "Posted");
      setBody(""); setOptions(["", ""]); setCloses(""); setADept(""); setALoc(""); setOpen(false); onPosted();
    },
    onError: (e) => toast.error(err(e, "Could not post")),
  });

  const submit = () => {
    const p: NewPost = { kind, body, audience_department: aDept || null, audience_location: aLoc || null };
    if (kind === "poll") {
      p.options = options.map((o) => o.trim()).filter(Boolean);
      p.closes_at = closes ? new Date(closes + "T23:59:00").toISOString() : null;
    }
    create.mutate(p);
  };
  const deptChoices = canModerate ? departments : (me?.department ? [me.department] : []);
  const locChoices = canModerate ? locations : (me?.location ? [me.location] : []);

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2">
        {(["post", "poll"] as const).map((k) => (
          <button key={k} type="button" onClick={() => { setKind(k); setOpen(true); }}
            className={`text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 ${kind === k ? "bg-primary-50 text-primary-700 font-semibold" : "text-gray-500 hover:bg-gray-50"}`}>
            {k === "post" ? <Newspaper className="w-3.5 h-3.5" /> : <BarChart3 className="w-3.5 h-3.5" />}{k === "post" ? "Post" : "Poll"}
          </button>
        ))}
      </div>
      <textarea className="input-base w-full text-sm min-h-[64px] resize-y" maxLength={3000} value={body} onFocus={() => setOpen(true)}
        placeholder={kind === "poll" ? "Ask the company a question…" : "Share an update with your colleagues…"} onChange={(e) => setBody(e.target.value)} />
      {open && kind === "poll" && (
        <div className="space-y-2">
          {options.map((o, i) => (
            <div key={i} className="flex gap-2">
              <input className={inputCls} maxLength={120} placeholder={`Option ${i + 1}`} value={o}
                onChange={(e) => setOptions((xs) => xs.map((x, j) => (j === i ? e.target.value : x)))} />
              {options.length > 2 && <button type="button" className="text-gray-400 hover:text-red-500" aria-label="Remove option"
                onClick={() => setOptions((xs) => xs.filter((_, j) => j !== i))}><X className="w-4 h-4" /></button>}
            </div>
          ))}
          <div className="flex flex-wrap items-end gap-3">
            {options.length < 6 && <button type="button" className="text-xs text-primary-600 flex items-center gap-1" onClick={() => setOptions((xs) => [...xs, ""])}>
              <Plus className="w-3.5 h-3.5" />Add option</button>}
            <Field label="Closes on (optional)" className="w-44">
              <input type="date" className={inputCls} value={closes} min={todayISO()} onChange={(e) => setCloses(e.target.value)} />
            </Field>
          </div>
        </div>
      )}
      {open && (
        <div className="flex flex-col sm:flex-row sm:items-end gap-2 sm:gap-3">
          <Field label="Audience: department" className="sm:w-48">
            <select className={inputCls} value={aDept} onChange={(e) => setADept(e.target.value)}>
              <option value="">Everyone</option>
              {deptChoices.map((d) => <option key={d} value={d}>{canModerate ? d : `Only ${d}`}</option>)}
            </select>
          </Field>
          <Field label="Audience: location" className="sm:w-48">
            <select className={inputCls} value={aLoc} onChange={(e) => setALoc(e.target.value)}>
              <option value="">Everywhere</option>
              {locChoices.map((d) => <option key={d} value={d}>{canModerate ? d : `Only ${d}`}</option>)}
            </select>
          </Field>
          <div className="sm:ml-auto flex gap-2">
            <button type="button" className="btn-secondary text-xs" onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="btn-primary text-xs flex items-center gap-1.5" disabled={!body.trim() || create.isPending} onClick={submit}>
              <Send className="w-3.5 h-3.5" />{create.isPending ? "Posting…" : kind === "poll" ? "Post poll" : "Post"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ══════════════════════════ card ══════════════════════════════════════════════

function FeedCard({ item, busy, onReact, onVote, onComments, onPin, onHide, onDelete }:
  { item: FeedItem; busy: boolean; onReact: (e: ReactionKey) => void; onVote: (o: string) => void; onComments: () => void;
    onPin: () => void; onHide: () => void; onDelete: () => void }) {
  const meta = KIND_META[item.kind] || KIND_META.post;
  const Icon = meta.icon;
  const auto = ["birthday", "anniversary", "new_joiner"].includes(item.kind);
  const who = auto ? item.subject?.name : item.author.name;
  return (
    <article className={`card space-y-3 ${item.hidden ? "opacity-70 ring-1 ring-red-200" : ""} ${item.pinned ? "ring-1 ring-primary-200" : ""}`}>
      <header className="flex items-start gap-3">
        <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-semibold ${meta.tone}`}>
          {auto || item.kind === "kudos" ? <Icon className="w-5 h-5" /> : initials(who)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-semibold text-sm text-gray-800 truncate">{item.kind === "kudos" ? `${item.author.name || "Someone"} → ${item.kudos?.to_name || ""}` : who || "Company feed"}</span>
            <span className="text-[11px] text-gray-400">{meta.label}{item.event_label ? ` · ${item.event_label}` : ""}</span>
          </div>
          <div className="text-[11px] text-gray-400 flex flex-wrap gap-x-2">
            <span>{ago(item.created_at)}</span>
            {item.department && <span>{item.department}</span>}
            {item.location && <span>{item.location}</span>}
            {(item.audience.department || item.audience.location) && (
              <span className="text-primary-600">For {[item.audience.department, item.audience.location].filter(Boolean).join(" · ")}</span>)}
          </div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {item.pinned && <span className="badge-teal text-[10px] flex items-center gap-1"><Pin className="w-3 h-3" />Pinned</span>}
          {item.hidden && <span className="badge-red text-[10px]">Hidden</span>}
        </div>
      </header>

      {item.hidden && item.hidden_reason && (
        <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">Hidden by {item.hidden_by || "HR"}: {item.hidden_reason}</p>)}

      <p className="text-sm text-gray-700 whitespace-pre-wrap break-words">{item.body}</p>

      {item.kind === "kudos" && item.kudos?.value_tag && (
        <div className="flex gap-2 text-xs"><span className="badge-purple">{item.kudos.value_tag}</span>
          {item.kudos.points > 0 && <span className="badge-amber">{item.kudos.points} points</span>}</div>)}

      {item.poll && (
        <div className="space-y-2">
          {item.poll.options.map((o) => {
            const showResults = !!item.poll!.my_vote || !item.poll!.open;
            const mine = item.poll!.my_vote === o.id;
            return showResults ? (
              <div key={o.id} className="relative rounded-lg border border-surface-border overflow-hidden">
                <div className={`absolute inset-y-0 left-0 ${mine ? "bg-primary-100" : "bg-gray-100"}`} style={{ width: `${o.pct}%` }} />
                <div className="relative flex justify-between gap-2 px-3 py-2 text-xs">
                  <span className={`truncate ${mine ? "font-semibold text-primary-700" : "text-gray-700"}`}>{o.label}{mine ? " · your vote" : ""}</span>
                  <span className="tabular-nums text-gray-500 flex-shrink-0">{o.pct}% · {o.count}</span>
                </div>
              </div>
            ) : (
              <button key={o.id} type="button" disabled={busy} onClick={() => onVote(o.id)}
                className="w-full text-left text-xs px-3 py-2 rounded-lg border border-surface-border hover:border-primary-400 hover:bg-primary-50 transition">{o.label}</button>
            );
          })}
          <p className="text-[11px] text-gray-400">{item.poll.total} vote{item.poll.total === 1 ? "" : "s"}
            {item.poll.closes_at ? ` · ${item.poll.open ? "closes" : "closed"} ${new Date(item.poll.closes_at).toLocaleDateString("en-CA", { day: "numeric", month: "short" })}` : ""}
            {!item.poll.open && " · results are final"}</p>
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-surface-border">
        {REACTIONS.map((r) => {
          const st = item.reactions?.[r] || { count: 0, mine: false };
          return (
            <button key={r} type="button" title={REACTION_LABELS[r].label} disabled={busy} onClick={() => onReact(r)}
              className={`text-xs px-2 py-1 rounded-full border flex items-center gap-1 transition ${st.mine ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-500 hover:bg-gray-50"}`}>
              <span aria-hidden>{REACTION_LABELS[r].glyph}</span>
              <span className="hidden sm:inline">{REACTION_LABELS[r].label}</span>
              {st.count > 0 && <span className="tabular-nums">{st.count}</span>}
            </button>
          );
        })}
        <button type="button" onClick={onComments} className="text-xs px-2 py-1 rounded-full text-gray-500 hover:bg-gray-50 flex items-center gap-1">
          <MessageSquare className="w-3.5 h-3.5" />{item.comments_count > 0 ? item.comments_count : ""}<span className="hidden sm:inline"> {item.comments_count === 1 ? "Comment" : "Comments"}</span>
        </button>
        <div className="ml-auto flex items-center gap-1">
          {item.can_moderate && (
            <>
              <IconBtn title={item.pinned ? "Unpin" : "Pin to top"} onClick={onPin}>{item.pinned ? <PinOff className="w-4 h-4" /> : <Pin className="w-4 h-4" />}</IconBtn>
              <IconBtn title={item.hidden ? "Unhide" : "Hide with a reason"} onClick={onHide}>{item.hidden ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}</IconBtn>
            </>
          )}
          {item.can_delete && <IconBtn title="Delete my post" onClick={onDelete} danger><Trash2 className="w-4 h-4" /></IconBtn>}
        </div>
      </footer>
    </article>
  );
}

function IconBtn({ title, onClick, children, danger }: { title: string; onClick: () => void; children: ReactNode; danger?: boolean }) {
  return <button type="button" title={title} aria-label={title} onClick={onClick}
    className={`p-1.5 rounded-lg text-gray-400 ${danger ? "hover:text-red-600 hover:bg-red-50" : "hover:text-gray-700 hover:bg-gray-100"}`}>{children}</button>;
}

// ══════════════════════════ comments drawer ═══════════════════════════════════

function CommentsDrawer({ item, canModerate, onClose, onHide, onChanged }:
  { item: FeedItem; canModerate: boolean; onClose: () => void; onHide: (cid: string) => void; onChanged: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const key = ["feed-comments", item.id];
  const list = useQuery({ queryKey: key, queryFn: () => feedApi.comments(item.id) });
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const done = () => { qc.invalidateQueries({ queryKey: key }); onChanged(); };
  const add = useMutation({
    mutationFn: () => feedApi.addComment(item.id, text),
    onSuccess: () => { setText(""); done(); },
    onError: (e) => toast.error(err(e, "Could not comment")),
  });
  const del = useMutation({ mutationFn: (cid: string) => feedApi.deleteComment(cid), onSuccess: done, onError: (e) => toast.error(err(e, "Could not delete")) });
  const unhide = useMutation({ mutationFn: (cid: string) => feedApi.moderateComment(cid, "unhide"), onSuccess: done, onError: (e) => toast.error(err(e, "Could not unhide")) });

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" onMouseDown={onClose}>
      <div className="bg-white h-full w-full sm:w-[600px] lg:w-[720px] shadow-xl flex flex-col" onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-start justify-between gap-3 px-4 py-3 border-b border-surface-border">
          <div className="min-w-0">
            <h3 className="font-display font-semibold text-gray-800">Comments</h3>
            <p className="text-xs text-gray-500 line-clamp-2">{item.body}</p>
          </div>
          <button type="button" className="text-gray-400 hover:text-gray-700" aria-label="Close" onClick={onClose}><X className="w-5 h-5" /></button>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {list.isLoading ? <div className="flex justify-center py-8"><Spinner /></div>
            : (list.data || []).length === 0 ? <Empty>No comments yet.</Empty>
            : (list.data || []).map((c: FeedComment) => (
              <div key={c.id} className={`flex gap-2.5 ${c.hidden ? "opacity-70" : ""}`}>
                <div className="w-8 h-8 rounded-full bg-gray-100 text-gray-600 text-xs font-semibold flex items-center justify-center flex-shrink-0">{initials(c.author.name)}</div>
                <div className="min-w-0 flex-1 bg-gray-50 rounded-lg px-3 py-2">
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="font-semibold text-gray-700 truncate">{c.author.name}</span>
                    <span className="text-gray-400">{ago(c.created_at)}</span>
                    {c.hidden && <span className="badge-red text-[10px]">Hidden</span>}
                    <span className="ml-auto flex gap-1">
                      {canModerate && (c.hidden
                        ? <IconBtn title="Unhide" onClick={() => unhide.mutate(c.id)}><Eye className="w-3.5 h-3.5" /></IconBtn>
                        : <IconBtn title="Hide with a reason" onClick={() => onHide(c.id)}><EyeOff className="w-3.5 h-3.5" /></IconBtn>)}
                      {c.can_delete && <IconBtn title="Delete my comment" danger onClick={() => { if (window.confirm("Delete this comment?")) del.mutate(c.id); }}><Trash2 className="w-3.5 h-3.5" /></IconBtn>}
                    </span>
                  </div>
                  {c.hidden && c.hidden_reason && <p className="text-[11px] text-red-600">Reason: {c.hidden_reason}</p>}
                  <p className="text-sm text-gray-700 whitespace-pre-wrap break-words">{c.body}</p>
                </div>
              </div>
            ))}
        </div>
        <footer className="border-t border-surface-border p-3 flex gap-2">
          <textarea className="input-base flex-1 text-sm min-h-[40px] max-h-32 resize-y" maxLength={1000} placeholder="Write a comment…" value={text}
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) add.mutate(); }} />
          <button type="button" className="btn-primary text-xs self-end flex items-center gap-1" disabled={!text.trim() || add.isPending} onClick={() => add.mutate()}>
            <Send className="w-3.5 h-3.5" /><span className="hidden sm:inline">Send</span></button>
        </footer>
      </div>
    </div>
  );
}

// ══════════════════════════ hide-with-reason modal ════════════════════════════

function HideModal({ target, onClose, onDone }: { target: { kind: "post" | "comment"; id: string }; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: async (): Promise<unknown> => (target.kind === "post" ? feedApi.moderate(target.id, "hide", reason) : feedApi.moderateComment(target.id, "hide", reason)),
    onSuccess: () => { toast.success("Hidden — the author has been told why"); onDone(); },
    onError: (e) => toast.error(err(e, "Could not hide")),
  });
  return (
    <Modal title={target.kind === "post" ? "Hide this post" : "Hide this comment"} sub="Employees stop seeing it. HR still sees it with your reason."
      onClose={onClose} width="max-w-3xl"
      footer={<>
        <button type="button" className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary text-xs" disabled={reason.trim().length < 3 || m.isPending} onClick={() => m.mutate()}>{m.isPending ? "Hiding…" : "Hide"}</button>
      </>}>
      <Field label="Reason (sent to the author)">
        <textarea className="input-base w-full text-sm min-h-[80px]" maxLength={300} autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Contains personal phone numbers — please repost without them" />
      </Field>
    </Modal>
  );
}

// ══════════════════════════ my feed settings ══════════════════════════════════

function SettingsPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ["feed-prefs"], queryFn: feedApi.prefs });
  const save = useMutation({
    mutationFn: feedApi.savePrefs,
    onSuccess: (p) => { qc.setQueryData(["feed-prefs"], p); toast.success("Saved"); onSaved(); },
    onError: (e) => toast.error(err(e, "Could not save")),
  });
  const rows: { k: "birthday" | "anniversary" | "new_joiner"; label: string; hint: string }[] = [
    { k: "birthday", label: "My birthday", hint: "Only the day and month are ever shown — never the year." },
    { k: "anniversary", label: "My work anniversary", hint: "e.g. “3 years at the company”." },
    { k: "new_joiner", label: "My welcome post", hint: "Posted in my first week." },
  ];
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">My feed settings</h3>
        <button type="button" className="text-gray-400 hover:text-gray-700" aria-label="Close" onClick={onClose}><X className="w-4 h-4" /></button>
      </div>
      {prefs.isLoading ? <Spinner className="h-5 w-5" /> : !prefs.data?.can_edit
        ? <p className="text-xs text-gray-500">Your login is not linked to an employee record, so there is nothing to post about you.</p>
        : rows.map((r) => {
          const shown = !prefs.data?.[r.k];
          return (
            <label key={r.k} className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={shown} disabled={save.isPending}
                onChange={(e) => save.mutate({ [r.k]: !e.target.checked })} />
              <span><span className="block text-sm text-gray-700">Celebrate {r.label.toLowerCase()}</span>
                <span className="block text-[11px] text-gray-400">{r.hint}</span></span>
            </label>
          );
        })}
    </div>
  );
}

// ══════════════════════════ home preview ══════════════════════════════════════

/** Latest 3 feed items for the employee home. `to` = the feed route. */
export function FeedPreview({ to = "/engage/feed" }: { to?: string }) {
  const q = useQuery({ queryKey: ["engage-feed-preview"], queryFn: () => feedApi.list({ limit: 3 }), staleTime: 60_000 });
  const items = q.data?.items || [];
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-display font-semibold text-sm text-gray-800 flex items-center gap-2"><Newspaper className="w-4 h-4 text-primary-500" />Company feed</h3>
        <Link to={to} className="text-xs text-primary-600 hover:underline">Open feed</Link>
      </div>
      {q.isLoading ? <div className="flex justify-center py-4"><Spinner className="h-5 w-5" /></div>
        : items.length === 0 ? <p className="text-xs text-gray-400 py-2">No posts yet.</p>
        : <ul className="divide-y divide-surface-border">
          {items.map((it) => {
            const meta = KIND_META[it.kind] || KIND_META.post;
            const Icon = meta.icon;
            return (
              <li key={it.id} className="py-2 flex gap-2.5">
                <div className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 ${meta.tone}`}><Icon className="w-3.5 h-3.5" /></div>
                <div className="min-w-0">
                  <p className="text-xs text-gray-700 line-clamp-2 break-words">{it.pinned && <Pin className="inline w-3 h-3 mr-1 text-primary-500" />}{it.body}</p>
                  <p className="text-[11px] text-gray-400">{meta.label}{it.event_label ? ` · ${it.event_label}` : ""} · {ago(it.created_at)}</p>
                </div>
              </li>
            );
          })}
        </ul>}
    </div>
  );
}
