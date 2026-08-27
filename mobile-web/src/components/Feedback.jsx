import { AlertCircle, Database } from "lucide-react";

export function LoadingState({ label = "正在讀取資料…" }) {
  return (
    <div className="feedback-card feedback-loading" role="status">
      <span className="loading-mark" aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <p>資料回來後會自動更新。</p>
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="feedback-card feedback-error" role="alert">
      <AlertCircle size={22} />
      <div>
        <strong>資料暫時無法讀取</strong>
        <p>{message}</p>
        {onRetry ? (
          <button className="text-button" type="button" onClick={onRetry}>
            重新讀取
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function EmptyState({ title, description }) {
  return (
    <div className="feedback-card feedback-empty">
      <Database size={22} />
      <div>
        <strong>{title}</strong>
        {description ? <p>{description}</p> : null}
      </div>
    </div>
  );
}
