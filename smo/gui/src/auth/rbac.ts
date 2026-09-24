// Role gating in the SPA evaluates the BFF's own permission table
// (GET /api/permissions), with the same first-match-wins rule as
// gui-bff/app/rbac.py — one table, not two to keep in sync. This only decides
// what to *show*; the BFF re-checks every call.

export type Role = "viewer" | "operator" | "admin";

export const ROLES: Role[] = ["viewer", "operator", "admin"];
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export interface PermissionRule {
  method: string;
  pattern: string;
  role: Role;
  queryMatch: Record<string, string>;
}

const compiled = new Map<string, RegExp>();
function regex(pattern: string): RegExp {
  let re = compiled.get(pattern);
  if (!re) {
    re = new RegExp(pattern);
    compiled.set(pattern, re);
  }
  return re;
}

export type QueryValues = Record<string, string | string[] | number | boolean | null | undefined>;

function values(query: QueryValues, key: string): string[] {
  const v = query[key];
  if (v === undefined || v === null) return [];
  return Array.isArray(v) ? v.map(String) : [String(v)];
}

/** The minimum role the table requires for this call, or null when the BFF
 * doesn't expose the route to anyone. */
export function requiredRole(rules: PermissionRule[], method: string, path: string, query: QueryValues = {}): Role | null {
  const m = method.toUpperCase();
  for (const rule of rules) {
    if (rule.method !== m || !regex(rule.pattern).test(path)) continue;
    if (Object.entries(rule.queryMatch ?? {}).some(([k, v]) => !values(query, k).includes(v))) continue;
    return rule.role;
  }
  return null;
}

export function roleAtLeast(role: Role, minimum: Role): boolean {
  return RANK[role] >= RANK[minimum];
}

export function can(rules: PermissionRule[], role: Role | undefined, method: string, path: string, query: QueryValues = {}): boolean {
  if (!role) return false;
  const needed = requiredRole(rules, method, path, query);
  return needed !== null && roleAtLeast(role, needed);
}
