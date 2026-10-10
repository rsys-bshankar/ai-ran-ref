/** The flow board hooks by flow id: the Lifecycle flows page calls the selected flow's hook only, so only that flow's sources load
 * (SCALE.md, Flows: 31 calls → 2–6). Every id in `lib/flows.ts` FLOWS has one. */
import { useFlow01 } from "./flow01";
import { useFlow02 } from "./flow02";
import { useFlow03 } from "./flow03";
import { useFlow04 } from "./flow04";
import { useFlow06 } from "./flow06";
import { useFlow07 } from "./flow07";
import { useFlow08 } from "./flow08";
import { useFlow09 } from "./flow09";
import { useFlow10 } from "./flow10";
import { useFlow15 } from "./flow15";
import { useFlow16 } from "./flow16";
import { useFlow19 } from "./flow19";
import type { FlowBoardHook } from "./types";

/** The hook of each flow. */
export const BOARDS: Record<string, FlowBoardHook> = {
  "01": useFlow01, "02": useFlow02, "03": useFlow03, "04": useFlow04, "06": useFlow06, "07": useFlow07,
  "08": useFlow08, "09": useFlow09, "10": useFlow10, "15": useFlow15, "16": useFlow16, "19": useFlow19,
};
