const crypto = require('crypto');
require('dotenv').config();

function generateSignedUrl(filename, expiresInSeconds = 3600) {
  const baseUrl = process.env.VIDEO_BASE_URL || 'https://fileserver01.apagaofogo.eco.br';
  const secret = process.env.VIDEO_SECRET_KEY || 'dfWLXc6rXNtbfsbpFnb16f3MEhggPJ5thQoL8wunKPuPvKxiMv7LNFqoWHRRe1Iu022WjYvp63sbTqLozpsyyFAn0VVxec80iHI8pu5g7QxTKLbPPahpggrcaY11S1WD';

  const expires = Math.floor(Date.now() / 1000) + expiresInSeconds;
  const data = `${filename}:${expires}`;

  const signature = crypto
    .createHmac('sha256', secret)
    .update(data)
    .digest('hex');

  const signedUrl = `${baseUrl}/file/${encodeURI(filename)}?expires=${expires}&signature=${signature}`;
  return signedUrl;
}

const video = 'AoF-C0029-APASC/AoF-C0029-APASC_det92_20250617_075144_wbbox.mp4';
const url = generateSignedUrl(video, 1800); // 30 minutos
console.log(`URL segura para "${video}":\n${url}`);
