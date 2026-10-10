/** The sign-in page (route /login, BRIEF §4 "Sign in", handoff `Login.dc.html`): a split screen with the brand panel on the left and the sign-in
 * form on the right; one column on a narrow screen (styles.css `.login-page`). A signed-in user is sent on to where they were going. Sections and
 * known limits: README.md. */
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { SectionBoundary } from "../../kit/SectionBoundary";
import { BrandPanel } from "./sections/BrandPanel";
import { SignInForm } from "./sections/SignInForm";

/** The page. */
export function Login() {
  const { me } = useAuth();
  const location = useLocation();
  if (me) return <Navigate to={(location.state as { from?: string } | null)?.from ?? "/"} replace />;
  return (
    <div className="login-page">
      <SectionBoundary id="login.brand"><BrandPanel /></SectionBoundary>
      <SectionBoundary id="login.form"><SignInForm /></SectionBoundary>
    </div>
  );
}
