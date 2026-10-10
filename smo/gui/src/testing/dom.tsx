/**
 * A small way to render a component in jsdom for a Vitest test, without a testing library: mount it, type into inputs, click, wait for the DOM to settle.
 * A test file that uses it starts with `// @vitest-environment jsdom`. Every helper runs inside React's `act`, so state updates are applied before the call returns;
 * `settle` additionally lets pending promises and zero-delay timers (fetch mocks, react-query) finish. Test code only; not part of the production bundle.
 */

import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export interface Mounted { container: HTMLElement; unmount: () => void }

const mounted: { root: Root; container: HTMLElement }[] = [];

/**
 * Renders `element` into a new container attached to the document and waits for the first render. The container is remembered so `cleanup` removes it.
 */
export async function mount(element: ReactElement): Promise<Mounted> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push({ root, container });
  await act(async () => { root.render(element); });
  return { container, unmount: () => { act(() => root.unmount()); container.remove(); } };
}

/** Unmounts and removes everything `mount` made; call it in `afterEach`. */
export function cleanup(): void {
  for (const { root, container } of mounted.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
}

/** Let promises and timers the component started finish (fetch mocks, React Query). */
export async function settle(times = 4): Promise<void> {
  for (let i = 0; i < times; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/**
 * Returns the first element matching `selector` whose trimmed text equals `text` (or matches the regular expression), or null.
 */
export function byText<T extends HTMLElement = HTMLElement>(root: ParentNode, selector: string, text: string | RegExp): T | null {
  const nodes = Array.from(root.querySelectorAll<T>(selector));
  return nodes.find((n) => (typeof text === "string" ? n.textContent?.trim() === text : text.test(n.textContent ?? ""))) ?? null;
}

/**
 * Sets an input's value and fires an `input` event. It goes through the native value setter, because React ignores a plain assignment to `value` on a controlled input.
 */
export async function type(input: HTMLInputElement, value: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Write into a textarea (`type` is for inputs). */
export async function typeArea(el: HTMLTextAreaElement, value: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Choose an option of a select by its value. */
export async function pick(select: HTMLSelectElement, value: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** The input, select or textarea inside the `<label class="field">` whose text starts with `label`. */
export function field<T extends HTMLElement = HTMLInputElement>(root: ParentNode, label: string): T {
  const wrapper = Array.from(root.querySelectorAll("label")).find((l) => l.querySelector(".field-label")?.textContent?.startsWith(label));
  if (!wrapper) throw new Error(`no field labelled ${label}`);
  return wrapper.querySelector("input, select, textarea") as T;
}

export async function click(el: HTMLElement): Promise<void> {
  await act(async () => { el.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
}

export async function submit(form: HTMLFormElement): Promise<void> {
  await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}
