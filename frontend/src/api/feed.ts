import api from "./client";

/** Company feed (employee portal batch 3, item 12). Server: ops service, /engage/feed/*.
 *  Birthdays are only ever "16 October" (day + month) — the API never sends a year or an age. */

export type FeedKind = "post" | "poll" | "birthday" | "anniversary" | "new_joiner" | "kudos";
export type FeedFilterKind = FeedKind | "celebrations";
export type ReactionKey = "like" | "celebrate" | "support" | "insightful";

export interface ReactionState { count: number; mine: boolean; }
export interface PollOption { id: string; label: string; count: number; pct: number; }
export interface Poll { options: PollOption[]; total: number; my_vote: string | null; closes_at: string | null; open: boolean; }

export interface FeedItem {
  id: string;
  kind: FeedKind;
  author: { employee_id: string | null; name: string | null };
  body: string;
  created_at: string | null;
  department: string | null;
  location: string | null;
  audience: { department: string | null; location: string | null };
  reactions: Record<ReactionKey, ReactionState>;
  comments_count: number;
  poll: Poll | null;
  pinned: boolean;
  hidden: boolean;
  hidden_reason?: string | null;
  hidden_by?: string | null;
  subject: { employee_id: string; name: string | null } | null;
  event_label: string | null;
  kudos: { to_name: string | null; value_tag: string | null; points: number } | null;
  can_delete: boolean;
  can_moderate: boolean;
  mine: boolean;
}

export interface FeedPage {
  items: FeedItem[];
  next_before: string | null;
  departments: string[];
  locations: string[];
  reactions: ReactionKey[];
  kinds: FeedFilterKind[];
  can_moderate: boolean;
  me: { department: string | null; location: string | null };
}

export interface FeedComment {
  id: string; item_id: string; author: { employee_id: string | null; name: string | null }; body: string;
  created_at: string | null; hidden: boolean; hidden_reason?: string | null; hidden_by?: string | null;
  can_delete: boolean; can_moderate: boolean;
}

export interface FeedPrefs { birthday: boolean; anniversary: boolean; new_joiner: boolean; can_edit: boolean; }

export interface NewPost {
  kind: "post" | "poll"; body: string; options?: string[]; closes_at?: string | null;
  audience_department?: string | null; audience_location?: string | null;
}

const q = (params: Record<string, any>) =>
  Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const enc = (id: string) => encodeURIComponent(id);

export const feedApi = {
  list: (p: { department?: string; location?: string; kind?: FeedFilterKind | ""; limit?: number; before?: string | null } = {}) =>
    api.get<FeedPage>("/engage/feed", { params: q(p) }).then((r) => r.data),
  create: (body: NewPost) => api.post<FeedItem>("/engage/feed", body).then((r) => r.data),
  react: (id: string, emoji: ReactionKey) =>
    api.post<{ item_id: string; on: boolean; reactions: Record<ReactionKey, ReactionState> }>(`/engage/feed/${enc(id)}/react`, { emoji }).then((r) => r.data),
  comments: (id: string) => api.get<{ item_id: string; comments: FeedComment[] }>(`/engage/feed/${enc(id)}/comments`).then((r) => r.data.comments),
  addComment: (id: string, body: string) => api.post<FeedComment>(`/engage/feed/${enc(id)}/comments`, { body }).then((r) => r.data),
  deleteComment: (cid: string) => api.delete(`/engage/feed/comments/${enc(cid)}`).then((r) => r.data),
  moderateComment: (cid: string, action: "hide" | "unhide", reason?: string) =>
    api.post<FeedComment>(`/engage/feed/comments/${enc(cid)}/${action}`, { reason: reason || null }).then((r) => r.data),
  vote: (id: string, option_id: string) => api.post<{ item_id: string; poll: Poll }>(`/engage/feed/${enc(id)}/vote`, { option_id }).then((r) => r.data),
  moderate: (id: string, action: "hide" | "unhide" | "pin" | "unpin", reason?: string) =>
    api.post<FeedItem>(`/engage/feed/${enc(id)}/${action}`, { reason: reason || null }).then((r) => r.data),
  remove: (id: string) => api.delete(`/engage/feed/${enc(id)}`).then((r) => r.data),
  prefs: () => api.get<FeedPrefs>("/engage/feed/prefs").then((r) => r.data),
  savePrefs: (p: Partial<Pick<FeedPrefs, "birthday" | "anniversary" | "new_joiner">>) =>
    api.put<FeedPrefs>("/engage/feed/prefs", p).then((r) => r.data),
};

export const REACTION_LABELS: Record<ReactionKey, { label: string; glyph: string }> = {
  like: { label: "Like", glyph: "👍" },
  celebrate: { label: "Celebrate", glyph: "🎉" },
  support: { label: "Support", glyph: "🤝" },
  insightful: { label: "Insightful", glyph: "💡" },
};

export const KIND_LABELS: Record<FeedFilterKind, string> = {
  post: "Posts", poll: "Polls", birthday: "Birthdays", anniversary: "Work anniversaries",
  new_joiner: "New joiners", kudos: "Kudos", celebrations: "Celebrations",
};
