const express = require("express");
const cors = require("cors");
const multer = require("multer");
const path = require("path");

const app = express();
app.use(cors());
app.use(express.json());
app.use("/uploads", express.static("uploads"));

const storage = multer.diskStorage({
  destination: "uploads/",
  filename: (req, file, cb) => {
    cb(null, Date.now() + path.extname(file.originalname));
  }
});
const upload = multer({ storage });

let posts = [];

app.post("/upload", upload.single("image"), (req, res) => {
  const imageURL =
    req.protocol +
    "://" +
    req.get("host") +
    "/uploads/" +
    req.file.filename;

  const post = {
    id: Date.now(),
    image: imageURL,
    created: new Date().toISOString(),
  };

  posts.unshift(post);
  res.json({ success: true, post });
});

app.get("/posts", (req, res) => {
  res.json(posts);
});

app.listen(3000, () => console.log("Zetagram backend ishlayapti!"));
