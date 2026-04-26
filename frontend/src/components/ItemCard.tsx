"use client";
import { Item, itemsApi } from "@/lib/api";

interface Props {
  item: Item;
  onDeleted: () => void;
}

export function ItemCard({ item, onDeleted }: Props) {
  async function handleDelete() {
    if (!confirm(`"${item.title}" wirklich löschen?`)) return;
    await itemsApi.delete(item.id);
    onDeleted();
  }

  return (
    <div className="flex items-start justify-between rounded-lg border bg-white p-4 shadow-sm">
      <div>
        <p className="font-medium">{item.title}</p>
        {item.description && (
          <p className="mt-1 text-sm text-gray-500">{item.description}</p>
        )}
        <p className="mt-2 text-xs text-gray-400">
          Erstellt {new Date(item.created_at).toLocaleString("de-DE")}
        </p>
      </div>
      <button
        onClick={handleDelete}
        className="ml-4 rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50"
      >
        Löschen
      </button>
    </div>
  );
}
