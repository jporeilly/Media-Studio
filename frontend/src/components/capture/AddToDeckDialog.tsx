/**
 * The Add to deck dialog (T3): choose a deck project, and the still becomes its last slide. ONE component,
 * opened from the Captures card and from the annotation editor's "Save and add to deck…" (T3b) - which is
 * why it lives on its own rather than inside the card. The picture added is whatever the still shows now
 * (the server's one oracle: the annotated export when there is one).
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../api/client";
import { Button, Modal, Select } from "../ui";
import { type Capture } from "../../lib/capture";
import { slidesQueryKey } from "../../lib/slides";

interface DeckProject { id: string; name: string; kind: string }

export function AddToDeckDialog({ capture, onClose, onNotice }: { capture: Pick<Capture, "id" | "name"> | null; onClose: () => void; onNotice: (text: string) => void }) {
  const qc = useQueryClient();
  const [deck, setDeck] = useState<string>("");
  const open = !!capture;
  // A fresh choice each time the dialog opens.
  useEffect(() => { if (open) setDeck(""); }, [open]);

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<{ projects: DeckProject[] }>("/api/projects"),
    enabled: open,
  });
  const decks = (projects.data?.projects ?? []).filter((p) => p.kind === "deck");

  const add = useMutation({
    mutationFn: ({ id, projectId }: { id: string; projectId: string }) =>
      api.post<{ index: number; slide_count: number }>(`/api/captures/${id}/add-to-deck`, { project_id: projectId }),
    onSuccess: (r, vars) => {
      onNotice(`Added as slide ${r.index + 1} of ${r.slide_count}.`);
      void qc.invalidateQueries({ queryKey: slidesQueryKey(vars.projectId) });
      void qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    },
    onError: (e) => onNotice(errorMessage(e)),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add to deck"
      width={480}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!deck || add.isPending} onClick={() => capture && add.mutate({ id: capture.id, projectId: deck })}>
            {add.isPending ? "Adding…" : "Add as last slide"}
          </Button>
        </>
      }
    >
      {projects.isSuccess && decks.length === 0 ? (
        <p>There is no deck project to add to. Import a .pptx first; a PDF's pages are fixed.</p>
      ) : (
        <>
          <p style={{ marginTop: 0 }}>The still becomes the deck's last slide, letterboxed to the deck's slide size when its shape differs, with empty notes to write.</p>
          <Select value={deck} onChange={(e) => setDeck(e.target.value)} aria-label="Deck">
            <option value="">Choose a deck…</option>
            {decks.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </>
      )}
    </Modal>
  );
}
