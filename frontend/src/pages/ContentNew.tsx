import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ImagePlus, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DEMO_TEXT } from "@/lib/mock";

export default function ContentNew() {
  const [params] = useSearchParams();
  const [text, setText] = useState(params.get("demo") === "1" ? DEMO_TEXT : "");
  const [channel, setChannel] = useState("instagram");
  const [confidential, setConfidential] = useState(false);
  return (
    <>
      <PageHeader title="New content" subtitle="Paste text, or add an image or PDF. We check it against the brand guidelines before anyone publishes." />
      <div className="glass-strong p-5 md:p-7">
        <Tabs defaultValue="text">
          <TabsList className="rounded-full bg-foreground/6 p-1">
            <TabsTrigger value="text" className="rounded-full px-4">Text</TabsTrigger>
            <TabsTrigger value="image" className="rounded-full px-4">Image</TabsTrigger>
            <TabsTrigger value="pdf" className="rounded-full px-4">PDF</TabsTrigger>
          </TabsList>
          <TabsContent value="text" className="mt-5 space-y-4">
            {params.get("demo") === "1" && <p className="rounded-2xl bg-[#0A84FF]/10 p-3 text-footnote text-[var(--accent-text)]">Demo post loaded. It is deliberately flawed so you can watch the check work.</p>}
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={8} aria-label="Content text" placeholder="Paste your caption, email or page copy" className="resize-none rounded-2xl bg-background/60 text-callout leading-7" />
            <div className="flex flex-wrap items-center gap-4">
              <Select value={channel} onValueChange={setChannel}>
                <SelectTrigger aria-label="Channel" className="w-[170px] rounded-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["instagram", "email", "website", "linkedin"].map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}
                </SelectContent>
              </Select>
              <label className="flex items-center gap-2 text-body"><Switch checked={confidential} onCheckedChange={setConfidential} /> Confidential</label>
            </div>
            {confidential && <p className="flex items-center gap-2 text-footnote text-muted-foreground"><ShieldCheck className="size-4 text-pass-text" strokeWidth={1.5} /> Only local checks will run. No outside AI sees this.</p>}
            <Button disabled={!text.trim()} onClick={() => toast.info("The check will be connected to the live pipeline in the next step.")} className="rounded-full bg-[#0A84FF] px-6 text-white hover:bg-[#0A84FF]/90">Check</Button>
          </TabsContent>
          {["image", "pdf"].map((t) => (
            <TabsContent key={t} value={t} className="mt-5">
              <div className="grid place-items-center rounded-2xl border border-dashed border-border px-6 py-14 text-center">
                <ImagePlus className="size-8 text-muted-foreground" strokeWidth={1.5} aria-hidden />
                <p className="mt-3 text-body font-medium">{t === "image" ? "PNG, JPG or WEBP, up to 10 MB" : "Text-based PDF, up to 20 pages"}</p>
                <p className="mt-1 text-footnote text-muted-foreground">Upload arrives when the check is connected.</p>
              </div>
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </>
  );
}
