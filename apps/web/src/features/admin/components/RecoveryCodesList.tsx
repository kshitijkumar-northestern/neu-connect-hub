/** The ten recovery codes from `adminApi.confirm()`, shown exactly once. Pure rendering: the
 * codes already arrive formatted (`XXXX-XXXX-XXXX`) from the server. */
export function RecoveryCodesList({ codes }: { codes: string[] }) {
  return (
    <ul className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-2xl border bg-muted/30 p-4 font-mono text-sm">
      {codes.map((code) => (
        <li key={code}>{code}</li>
      ))}
    </ul>
  );
}
