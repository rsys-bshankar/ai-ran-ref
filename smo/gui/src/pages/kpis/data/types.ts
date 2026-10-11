/** View-model types of the KPIs & Assurance page that `api/types.ts` does not carry: a computed KPI (ran-nf-oam/app/kpi.py `compute`), a KPI series (`series`) and the
 * TS 28.104 MDA resources of MDAF (mdaf/app/mda.py `_function_view`, `_request_view`, `_report_view_for`). */

/** One group of a computed KPI; `value` is null with a `reason` (NO_DATA, UNDEFINED) when it cannot be computed. */
export interface KpiGroupValue { group: Record<string, string>; value: number | null; samples: number; reason: string | null }

/** `GET /ran-nf-oam/kpis/{name}`. */
export interface KpiResult { kpi: string; unit: string | null; from: string; to: string; groupBy: string; filesScanned: number; truncated: boolean; items: KpiGroupValue[] }

/** One step of a KPI series: `at` is the step's start; `value` is null with a `reason` when the step has no data. */
export interface KpiPoint { at: string; value: number | null; samples: number; reason: string | null }

/** `GET /ran-nf-oam/kpis/{name}/series` (GUI-4.1). */
export interface KpiSeries { kpi: string; unit: string | null; from: string; to: string; stepSeconds: number; filesScanned: number; truncated: boolean; points: KpiPoint[] }

/** An MDA function (the analytics an MDAF instance can run). */
export interface MdaFunction { id: string; attributes: { userLabel: string | null; supportedMDACapabilities: string[]; supportedMDADomain: string | null; mLModelRefList: string[]; aIMLInferenceFunctionRefList: string[] } }

/** An MDA request (what an operator or rApp asked MDAF to analyse). */
export interface MdaRequest {
  id: string;
  attributes: {
    mDAFunctionRef: string | null; requestedMDAOutputs: { mDAType: string }[]; reportingMethod: string; reportingTarget: string | null;
    analyticsScope: { managedEntitiesScope?: string[] | null; areaScope?: unknown[] | null } | null; startTime: string | null; stopTime: string | null;
    requestedBy: string | null; active: boolean;
  };
}

/** An MDA report. */
export interface MdaReport {
  id: string;
  attributes: { mDAOutputs: unknown; mDARequestRef: string | null; mDAFunctionRef: string | null; reportKind: string; scope: unknown; deliveredToRequestRefList: string[]; generatedAt: string | null };
}
