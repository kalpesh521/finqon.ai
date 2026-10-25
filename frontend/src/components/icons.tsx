type IconProps = { className?: string };

export function Mark({ className = "h-9 w-9" }: IconProps) {
    return <img src="/logo-mark.png" alt="" className={`${className} object-contain`} />;
}

export function Wordmark({ className = "" }: { className?: string }) {
    return (
        <span className={className}>
            FinQon<span className="text-[#2AD7EA]">.ai</span>
        </span>
    );
}

export function MenuIcon({ className = "h-5 w-5" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M5 7h14M5 12h14M5 17h10" strokeLinecap="round" />
        </svg>
    );
}

export function SendIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M5 12h12M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function PlusIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M12 5v14M5 12h14" strokeLinecap="round" />
        </svg>
    );
}

export function EllipsisIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
            <circle cx="6" cy="12" r="1.4" />
            <circle cx="12" cy="12" r="1.4" />
            <circle cx="18" cy="12" r="1.4" />
        </svg>
    );
}

export function PencilIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M4 20h4l10-10-4-4L4 16v4Z" strokeLinejoin="round" />
            <path d="m12.5 7.5 4 4" />
        </svg>
    );
}

export function PinIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M9 10.5 7 17l6.5-2" strokeLinejoin="round" />
            <path d="m14 4 6 6-3 1-4 4-1 3-6-6 3-1 4-4 1-3Z" strokeLinejoin="round" />
        </svg>
    );
}

export function TrashIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M5 7h14" strokeLinecap="round" />
            <path d="M9 7V5h6v2" strokeLinejoin="round" />
            <path d="m8 7 1 12h6l1-12" strokeLinejoin="round" />
        </svg>
    );
}

export function ShieldIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M12 3 5 6v6c0 4.2 2.8 7.4 7 9 4.2-1.6 7-4.8 7-9V6l-7-3Z" strokeLinejoin="round" />
            <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function PanelIcon({ className = "h-4 w-4", collapsed = false }: IconProps & { collapsed?: boolean }) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
            <path d="M9 4.5v15" />
            {collapsed ? (
                <path d="m13 10 2.2 2-2.2 2" strokeLinecap="round" strokeLinejoin="round" />
            ) : (
                <path d="m15.2 10-2.2 2 2.2 2" strokeLinecap="round" strokeLinejoin="round" />
            )}
        </svg>
    );
}

export function ChevronIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function CodeIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="m8 8-4 4 4 4M16 8l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function EyeIcon({ className = "h-4 w-4", off = false }: IconProps & { off?: boolean }) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
            <circle cx="12" cy="12" r="2.5" />
            {off ? <path d="M4 4l16 16" strokeLinecap="round" /> : null}
        </svg>
    );
}

export function UploadCloudIcon({ className = "h-8 w-8" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M7 18a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 8.5a4.75 4.75 0 0 1 .5 9.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M12 20v-8m0 0-3 3m3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function PaperclipIcon({ className = "h-5 w-5" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <path d="m20 11-8.2 8.2a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 6.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function FileIcon({ className = "h-5 w-5" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" strokeLinejoin="round" />
            <path d="M14 3v5h5M9 13h6M9 17h4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function CheckCircleIcon({ className = "h-5 w-5" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="m8 12.5 2.8 2.8L16 9.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export function CloseIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="m6 6 12 12M18 6 6 18" strokeLinecap="round" />
        </svg>
    );
}

export function RetryIcon({ className = "h-4 w-4" }: IconProps) {
    return (
        <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M20 12a8 8 0 1 1-2.6-5.9M20 4v5h-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}
