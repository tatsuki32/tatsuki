// The plans. The landing page, the app's limits and /admin all read this one list.
export const PLANS = {
  free: { id: "free", name: "Free", month: 0, year: 0, feeds: 5, ai: false, blurb: "Try it with a few sources.", perks: ["5 sources", "Daily email with titles and links", "Folders, Later and Read marks"] },
  pro: { id: "pro", name: "Pro", month: 4, year: 40, feeds: 50, ai: true, blurb: "Everything, with AI summaries.", perks: ["50 sources", "A one-line summary for every article", "A short brief of the whole day at the top", "Save to tasks"] },
};

export const plan = (id) => PLANS[id] || PLANS.free;

// Claude Haiku, USD per million tokens (desk/lifelog.py PRICES, read 2026-11-05)
export const AI_MODEL = "claude-haiku-5-5";
export const AI_PRICE = { in: 0.1, out: 0.5 };

// competitor prices shown on the landing page: third-party comparison pages, checked 2026-11-15, not the vendors' own pricing pages
export const COMPETITORS = [
  { name: "Feedly", price: "$6.99/mo Pro, $12.99/mo with AI", note: "A reader you go and open." },
  { name: "Inoreader", price: "$9.99/mo Pro", note: "Powerful, with many settings." },
];
