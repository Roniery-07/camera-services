const express = require('express');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const dotenv = require('dotenv');

dotenv.config();

const app = express();

const PORT = process.env.PORT || 8000;
const VIDEO_DIR = process.env.VIDEO_DIR || '/app/videos';
const SECRET_KEY = process.env.SECRET_KEY || 'dfWLXc6rXNtbfsbpFnb16f3MEhggPJ5thQoL8wunKPuPvKxiMv7LNFqoWHRRe1Iu022WjYvp63sbTqLozpsyyFAn0VVxec80iHI8pu5g7QxTKLbPPahpggrcaY11S1WD';

app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.url}`);
  next();
});

app.get('/file/*', (req, res) => {
  const filename = req.params[0];
  const { expires, signature } = req.query;

  if (!filename || !expires || !signature) {
    return res.status(400).json({ success: false, message: "Missing parameters." });
  }

  const now = Math.floor(Date.now() / 1000);
  if (now > parseInt(expires)) {
    return res.status(403).json({ success: false, message: "Link expired." });
  }

  const expectedSignature = crypto
    .createHmac('sha256', SECRET_KEY)
    .update(`${filename}:${expires}`)
    .digest('hex');

  if (signature !== expectedSignature) {
    return res.status(403).json({ success: false, message: "Invalid signature." });
  }

  const filePath = path.join(VIDEO_DIR, filename);

  if (!filePath.startsWith(path.resolve(VIDEO_DIR))) {
    return res.status(403).json({ success: false, message: "Access denied." });
  }

  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ success: false, message: "File not found." });
  }

  res.sendFile(filePath);
});

app.listen(PORT, () => {
  console.log(`Servidor de vídeos escutando na porta ${PORT}`);
});

