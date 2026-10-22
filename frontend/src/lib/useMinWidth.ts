import { useEffect, useState } from "react";

export function useMinWidth(width: number): boolean {
    const query = `(min-width: ${width}px)`;
    const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

    useEffect(() => {
        const media = window.matchMedia(query);
        const update = () => setMatches(media.matches);
        update();
        media.addEventListener("change", update);
        return () => media.removeEventListener("change", update);
    }, [query]);

    return matches;
}
