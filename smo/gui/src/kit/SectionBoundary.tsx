/** An error boundary around one box of a page (STRUCTURE.md rule 1): a crash while rendering a section shows an error with a retry in
 * that box and the rest of the page keeps working. The section id (`alarms.table`) goes on the wrapper as `data-section`, the id used in
 * logs, tests and the page's README. */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { id: string; children: ReactNode }
interface State { error: Error | null }

/** The boundary. Retry re-mounts the section. */
export class SectionBoundary extends Component<Props, State> {
  state: State = { error: null };

  /** Catches a render error of a child section. */
  static getDerivedStateFromError(error: Error): State { return { error }; }

  /** Logs the crash with the section id, so a report names the box that failed. */
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`section ${this.props.id} crashed`, error, info.componentStack);
  }

  /** The section, or its error box. */
  render() {
    if (this.state.error) {
      return (
        <section className="card" data-section={this.props.id}>
          <div className="error-box row between" role="alert">
            <span>This box failed to render: {this.state.error.message}</span>
            <button type="button" className="btn small" onClick={() => this.setState({ error: null })}>Retry</button>
          </div>
        </section>
      );
    }
    return this.props.children;
  }
}
