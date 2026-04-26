/**
 * Seite: Neuen Import starten (/imports/new)
 */

import ImportForm from "@/components/imports/ImportForm";

export default function NewImportPage() {
  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">Neuer Import</h1>
      <p className="text-sm text-gray-500">
        Gib Firmenname und Jahr an. Der Ordner{" "}
        <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-xs">
          Firmenname_Jahr
        </code>{" "}
        wird automatisch verwendet und angelegt, falls er noch nicht existiert.
        Lege die PDF-Dateien in diesen Ordner, bevor du den Import startest.
      </p>
      <ImportForm />
    </div>
  );
}
