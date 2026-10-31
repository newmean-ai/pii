package ai.newmean.pii;

import static org.junit.Assert.assertEquals;
import static org.junit.Assume.assumeTrue;

import android.content.Context;
import java.io.File;
import java.io.IOException;
import java.util.List;
import org.junit.Test;

// The library from Java (static factories, overloads, AutoCloseable), as in the README.
public class JavaApiTest {
    @SuppressWarnings("unused")
    private static String readmeContextCall(Context context, String text) throws IOException {
        try (PiiRedactor redactor = PiiRedactor.createBlocking(context)) {
            return redactor.redact(text).getText();
        }
    }

    @Test
    public void fromJava() throws IOException {
        String lib = System.getProperty("ai.newmean.pii.library");
        String models = System.getProperty("pii.models");
        assumeTrue(lib != null && new File(lib).isFile() && models != null);
        File dir = new File(models, "pii-nano");
        File model = new File(dir, PiiModels.modelPath(PiiVariant.Q4));
        assumeTrue(model.isFile());
        try (PiiRedactor r = PiiRedactor.createBlocking(model, new File(dir, PiiModels.TOKENIZER_PATH), PiiSize.NANO)) {
            String text = "Mail kim.minjun@example.com";
            List<PiiSpan> spans = r.detect(text);
            assertEquals("EMAIL", spans.get(0).getLabel());
            assertEquals("Mail [EMAIL]", r.redact(text).getText());
            assertEquals("Mail " + "#".repeat(22), r.redact(text, RedactMode.MASK, "#").getText());
            assertEquals("Mail [EMAIL]", PiiRedactor.applyRedaction(text, spans));
            assertEquals(PiiSize.MEDIUM, PiiModels.DEFAULT_SIZE);
            assertEquals(13, PiiLabels.ALL.size());
        }
    }
}
