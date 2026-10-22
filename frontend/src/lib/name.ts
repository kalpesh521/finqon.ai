export function greetingName(user: { display_name?: string | null; email: string } | null): string {
    const named = user?.display_name?.trim();
    if (named) return named.split(/\s+/)[0] ?? named;
    const local = user?.email.split("@")[0] ?? "";
    const word = local.split(/[._+-]/)[0] ?? "";
    if (!word) return "there";
    return word.charAt(0).toUpperCase() + word.slice(1);
}
