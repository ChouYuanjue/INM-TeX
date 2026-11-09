# Experimental provenance for N-20261009

All three figure assets are unmodified, SHA-256-identical copies of original BiSem/NineLab analysis figures from:

`E:\bisem\reports\BiSem_Weekly_2026-10-26_to_11-01\images\`

| Committed figure | SHA-256 (hex) |
|---|---|
| `05_high_motion_person_worst_case.png` | `cfc06247cca6269077acf7f659c1f138cf94cbbd39ff41cfd6d6d86c87e26d80` |
| `06_person_latent_drift.png` | `0c5e610a33b5377c689492442644248019eb7f0ee6a77cedee8cdc4236b666bf` |
| `08_residual_person_psnr_curve.png` | `ae413c636a749a87055a5d6e51450b0289fe004dc8b181bd1f2452102fd228c7` |

## Numeric sources

- `E:\bisem\reports\BISEM_THREE_AXES_PERSON_ROI_LATENT_SIZE_20261030.md`: E9 ViT layer-wise pseudo-label linear probes, 41 held-out frames / 7,697 patches / 732 positives. AP values used in the manuscript: coordinate 0.430, embedding 0.338, blocks 3/6/9/12 = 0.789/0.777/0.772/0.743. Also the real high-motion person ROI reconstruction PSNR pair 27.88 and 19.52 dB, and exploratory Spearman rho approximately -0.65.
- `E:\BiSem_Closeout_20261031\BISEM_E9_AUDITED_CLOSEOUT_20261031.md`: VAL115 full-frame PSNR 31.800 to 31.869 dB; person ROI mean +0.174 dB, median +0.038 dB across 515 person observations on 144 frames. Final-layer person-patch AP comparison: original E9 0.7416, K12 0.7340, K12 plus residual 0.7329.

## Interpretation limits

- The YOLO26n detector acted on the original 1080p video to define fixed ROIs or pseudo-labels; these are not independently annotated semantic ground truth.
- Patch probes evaluate linear readout on a specified held-out recording with a static camera and do not estimate information-theoretic mutual information.
- Person detections across successive frames are temporally correlated and are not independent video-level observations.
- The experiments postdate the October 9 archival date of the mathematical note and are presented explicitly as retrospective case studies, not as results available on October 9.
