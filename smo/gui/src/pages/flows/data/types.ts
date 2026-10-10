/** View-model types of the Lifecycle flows page (`pages/flows`): what one flow board hook (`data/flowNN.tsx`) hands the sections. */
import type { ReactNode } from "react";

import type { FlowStep } from "../../../lib/flows";

/** One subject a flow can follow (a package, a model, an NF deployment …): its id (the `?subject=` value) and the text the picker shows. */
export interface Subject { id: string; label: string }

/** What a flow board needs: the subject list, the chosen subject, its evaluated steps and the actions of each step. */
export interface FlowBoardData {
  /** Every subject the picker offers; undefined while the list loads. */
  subjects: Subject[] | undefined;
  /** The list's error, if it failed, and how to retry it. */
  subjectsError?: unknown;
  retry?: () => void;
  /** The subject followed: the `?subject=` one, or the newest (the list's last) when none is chosen. */
  selected: Subject | undefined;
  /** The steps of the selected subject, from `lib/flows.ts`. */
  steps: FlowStep[];
  /** Buttons or links per step id, shown on the current, warned or failed step (and on the steps in `alwaysActions`). */
  actions: Record<string, ReactNode>;
  /** Step ids whose actions show whatever their status (optional branches such as flow 02's end of life). */
  alwaysActions?: string[];
  /** What to show when there is no subject at all (with a way to make one). */
  empty: ReactNode;
  /** More detail under the steps (a config job's sub-changes …). */
  extra?: ReactNode;
}

/** A board hook: given the `?subject=` id (or null), loads only this flow's sources for that subject. */
export type FlowBoardHook = (subjectId: string | null) => FlowBoardData;
