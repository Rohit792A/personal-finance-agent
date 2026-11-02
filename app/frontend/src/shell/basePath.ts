/**
 * Vite's base path without the trailing slash: '' when the app is served from the site root
 * (Firebase Hosting, the dev server), '/personal-finance-agent' for the recorded demo on GitHub Pages.
 * The router uses it as its basename.
 */
export const BASE_PATH = import.meta.env.BASE_URL.replace(/\/+$/, '');

/** A window path as the router sees it: the base path removed ('/personal-finance-agent/budgets' -> '/budgets'). */
export function routerPathname(pathname: string): string {
  if (!BASE_PATH) return pathname;
  if (pathname === BASE_PATH) return '/';
  return pathname.startsWith(`${BASE_PATH}/`) ? pathname.slice(BASE_PATH.length) : pathname;
}
