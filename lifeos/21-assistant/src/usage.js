// A month's cost, worked out from what each API response reports. An estimate: the Console has the real bill.
const PRICES = { // USD per million tokens: input, output, cache read
  "claude-opus-5-5": [4, 20, 0.2],
  "claude-opus-5": [5, 25, 0.5],
  "claude-opus-4-8": [5, 25, 0.5],
  "claude-sonnet-5-5": [2, 10, 0.2],
  "claude-haiku-5-5": [0.1, 0.5, 0.01],
};
const UNKNOWN_MODEL = [5, 25, 0.5];
const CACHE_WRITE = 1.25;
const SEARCH_USD = 0.01; // $10 per 1,000 searches

export function costOf(model, u = {}) {
  const [input, output, read] = PRICES[model] || UNKNOWN_MODEL;
  const tokens =
    (u.input_tokens || 0) * input +
    (u.output_tokens || 0) * output +
    (u.cache_read_input_tokens || 0) * read +
    (u.cache_creation_input_tokens || 0) * input * CACHE_WRITE;
  return tokens / 1e6 + (u.server_tool_use?.web_search_requests || 0) * SEARCH_USD;
}

export const limitOf = (env) => Number(env.MONTHLY_LIMIT_USD) || 10;
