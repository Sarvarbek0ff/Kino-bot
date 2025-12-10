<!DOCTYPE html>
<html>
<head>
    <title>Zetagram</title>
    <style>
        body { 
            font-family: Arial; 
            background:#fafafa; 
            margin:0; 
        }
        .top { 
            background:white; 
            padding:15px; 
            font-size:25px; 
            font-weight:bold; 
            text-align:center; 
            border-bottom:1px solid #ddd; 
        }
        .post { 
            background:white; 
            margin:20px auto; 
            width:95%; 
            border-radius:10px; 
            padding:10px; 
            box-shadow:0 0 5px #ccc; 
        }
        img { 
            width:100%; 
            border-radius:10px; 
        }
        .upload-box { 
            width:95%; 
            margin:20px auto; 
            background:white; 
            padding:10px; 
            border-radius:10px; 
            box-shadow:0 0 5px #ccc; 
        }
    </style>
</head>
<body>

<div class="top">Zetagram</div>

<div class="upload-box">
    <input type="file" id="image">
    <button onclick="upload()">Upload Post</button>
</div>

<div id="feed"></div>

<script>
const API = "https://zetagram--sarvarbekxc.replit.app";

function upload() {
    let file = document.getElementById("image").files[0];
    let f = new FormData();
    f.append("image", file);

    fetch(API + "/upload", {
        method: "POST",
        body: f
    })
    .then(res => res.json())
    .then(() => loadPosts());
}

function loadPosts() {
    fetch(API + "/posts")
    .then(res => res.json())
    .then(data => {
        document.getElementById("feed").innerHTML = "";
        data.forEach(p => {
            document.getElementById("feed").innerHTML += `
                <div class='post'>
                    <img src="${p.image}">
                </div>
            `;
        });
    });
}

loadPosts();
</script>

</body>
</html>
