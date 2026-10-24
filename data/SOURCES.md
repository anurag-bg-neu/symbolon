# Public data sources

All repository data is synthetic or public reference metadata. No patient data is included.

## Carrier panel

`panel.json` contains exactly 30 autosomal-recessive gene-condition entries, within the design requirement of 30–60. Every condition summary is paraphrased from and links to its corresponding [MedlinePlus Genetics condition page](https://medlineplus.gov/genetics/condition/). Links and autosomal-recessive inheritance statements were checked on October 24, 2026. BRCA1 and BRCA2 are excluded because GeneMatch models them as separate germline trial claims rather than recessive carrier-panel entries.

## Synthetic whole genomes

`synthetic_genomes/assembly.json` uses chromosome names and lengths associated with the NCBI [GRCh38.p14 accession GCF_000001405.40](https://www.ncbi.nlm.nih.gov/refseq/annotation_euk/Homo_sapiens/GCF_000001405.40-RS_2025_08/). It copies no reference sequence. The documented SHA-256 counter recipe deterministically defines every base of two entirely synthetic haplotypes for Alex and Sam. Their simulated carrier and trial labels are scenario inputs, not variant interpretations derived from those bases.

A materialized FASTA would be several gigabytes per sample and must be written under gitignored `var/`, not committed to `data/`.

## Optional Kiro MCP source access

For future source review, add the official, read-only Fetch MCP server through **Kiro: Open workspace MCP config (JSON)**. Keep calls approval-gated:

```json
{
  "mcpServers": {
    "public-reference-fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch@2026.10.19"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

This package is published by the Model Context Protocol project under the MIT license. It can make outbound requests to public URLs; private-address access remains blocked by default. Do not use it with patient data or credentials.

Content was rephrased for compliance with licensing restrictions.
